import asyncio

from nonebot import get_app
from nonebot import logger

from plugins.config import (
    get_active_channels,
    get_groups_for_channel,
    get_all_channels,
    get_backfill_enabled,
)
from plugins.history import (
    load_last_messages,
    save_last_messages,
)
from plugins.json_io import load_json
from plugins.fetch import (
    fetch_channel_latest,
    fetch_channel_gap_count,
    _resolve_channel_name,
)
from plugins.backfill import _backfill_missed
from plugins.dedup import (
    normalize_channel_map,
    compute_base_id,
    apply_baseline,
)


# =====================================================
# bot HTTP 接口（供管理面板调用）
#
# 面板与 bot 通过 HTTP 通信（bot 的 NoneBot FastAPI 运行在
# 127.0.0.1:8082）。提供端点：
#   - GET  /api/backfill/pending  各启用频道的待补发缺口数
#   - POST /api/backfill/run      手动触发一轮补发（尊重总开关）
#   - POST /api/backfill/clear    清除待补发（把记录推进到最新，不发送）
#   - GET  /api/channel-names     各频道 Discord 真实名称（面板展示用）
#   - GET  /api/channels/audit    上次频道权限扫描快照（可读频道列表）
#   - POST /api/channels/refresh  重新扫描频道权限并更新记录
# =====================================================

app = get_app()


@app.get("/api/backfill/pending")
async def api_backfill_pending():
    """统计各启用频道的待补发缺口数（取最落后群的记录为基准）

    返回 {"ok": True, "total": int, "channels": {频道ID: {"name": str, "count": int}}}
    count 达到上限 1000 时表示"至少 1000 条"；统计失败记 -1"""
    channels = get_active_channels()

    if not channels:
        return {"ok": True, "total": 0, "channels": {}}

    last_messages = load_last_messages()

    result = {}
    total = 0

    for channel_id, channel_name in channels.items():

        # 每个频道独立路由：待补发统计只覆盖该频道命中转发组的群
        active_groups = get_groups_for_channel(channel_id)

        if not active_groups:

            result[channel_id] = {"name": channel_name, "count": 0}
            continue

        channel_map = normalize_channel_map(
            last_messages,
            channel_id,
        )

        base_id = compute_base_id(
            channel_map,
            active_groups,
        )

        if base_id is None:
            result[channel_id] = {"name": channel_name, "count": 0}
            continue

        try:

            latest = await fetch_channel_latest(
                channel_id
            )

            if (
                not latest
                or int(latest) <= int(base_id)
            ):
                count = 0
            else:
                count = await fetch_channel_gap_count(
                    channel_id,
                    base_id,
                )

                if count is None:
                    count = -1

        except Exception:
            logger.exception(
                "统计待补发数量失败: {}",
                channel_id
            )
            count = -1

        result[channel_id] = {
            "name": channel_name,
            "count": count,
        }

        if count > 0:
            total += count

    return {
        "ok": True,
        "total": total,
        "channels": result,
    }


@app.post("/api/backfill/run")
async def api_backfill_run():
    """手动触发一轮补发（尊重补发总开关）"""
    if not get_backfill_enabled():
        return {"ok": False, "msg": "补发功能已关闭"}
    await _backfill_missed()
    return {"ok": True, "msg": "补发已触发"}


@app.post("/api/backfill/clear")
async def api_backfill_clear():
    """清除待补发：把各启用频道的记录推进到最新消息 ID（不发送消息）"""
    channels = get_active_channels()

    if not channels:
        return {"ok": True, "cleared": 0}

    last_messages = load_last_messages()

    cleared = 0

    for channel_id in channels:

        # 只清除该频道命中转发组的群的待补发
        active_groups = get_groups_for_channel(channel_id)

        if not active_groups:
            continue

        channel_map = normalize_channel_map(
            last_messages,
            channel_id,
        )

        latest = await fetch_channel_latest(
            channel_id
        )

        if not latest:
            continue

        if apply_baseline(
            channel_map,
            active_groups,
            latest,
            include_star=True,
        ):
            cleared += 1

    if cleared:
        save_last_messages(
            last_messages
        )

        logger.info(
            "清除待补发: {} 个频道记录已推进到最新",
            cleared
        )

    return {"ok": True, "cleared": cleared}


@app.get("/api/channel-names")
async def api_channel_names():
    """返回设置文件中全部频道的 Discord 真实名称（供面板展示）

    返回 {"ok": True, "names": {频道ID: 真实名或null}}；
    API 拉取失败的频道值为 null（复用 fetch.py 进程内缓存）"""
    channels = get_all_channels()

    if not channels:
        return {"ok": True, "names": {}}

    results = await asyncio.gather(
        *(_resolve_channel_name(channel_id) for channel_id in channels)
    )

    return {
        "ok": True,
        "names": dict(zip(channels, results)),
    }


# =====================================================
# 频道权限审计（供面板"刷新频道记录"）
#
# GET  /api/channels/audit    读取上次扫描的结构化快照
# POST /api/channels/refresh  运行诊断脚本重新扫描并更新记录
# =====================================================

AUDIT_FILE = "data/channels_audit.json"


def _read_audit():
    """读取 data/channels_audit.json；不存在/损坏返回空结构"""
    data = load_json(
        AUDIT_FILE,
        default=None
    )
    if isinstance(data, dict):
        return data
    return {
        "generated_at": "",
        "visible_total": 0,
        "readable_total": 0,
        "readable": [],
        "visible": [],
    }


@app.get("/api/channels/audit")
async def api_channels_audit():
    """返回上次频道权限扫描的结构化快照（面板打开时展示可读频道）"""
    return {"ok": True, "audit": _read_audit()}


@app.post("/api/channels/refresh")
async def api_channels_refresh():
    """重新扫描频道权限并更新 docs/CHANNELS.md 与 data/channels_audit.json

    通过 subprocess 复用 scripts/diagnose_channels.py（避免重复实现），
    返回最新可读频道列表供面板展示。"""
    import os
    import sys

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(base, "scripts", "diagnose_channels.py")

    if not os.path.exists(script):
        return {"ok": False, "msg": "诊断脚本不存在"}

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            script,
            cwd=base,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=120,
        )
    except asyncio.TimeoutError:
        return {"ok": False, "msg": "刷新超时"}
    except Exception as exc:
        return {"ok": False, "msg": f"执行失败: {exc}"}

    if proc.returncode != 0:
        return {"ok": False, "msg": (stderr.decode("utf-8", "ignore") or "执行失败")[:200]}

    out = stdout.decode("utf-8", "ignore").strip()
    summary = out.splitlines()[-1] if out else "已刷新"
    return {
        "ok": True,
        "msg": summary,
        "audit": _read_audit(),
    }
