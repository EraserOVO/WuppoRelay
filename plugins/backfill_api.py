from nonebot import get_app
from nonebot import logger

from plugins.config import (
    get_active_channels,
    get_active_groups,
    get_backfill_enabled,
)
from plugins.history import (
    load_last_messages,
    save_last_messages,
)
from plugins.fetch import (
    fetch_channel_latest,
    fetch_channel_gap_count,
)
from plugins.backfill import _backfill_missed


# =====================================================
# 补发 HTTP 接口（供管理面板调用）
#
# 面板与 bot 通过 HTTP 通信（bot 的 NoneBot FastAPI 运行在
# 127.0.0.1:8082）。提供三个端点：
#   - GET  /api/backfill/pending  各启用频道的待补发缺口数
#   - POST /api/backfill/run      手动触发一轮补发（尊重总开关）
#   - POST /api/backfill/clear    清除待补发（把记录推进到最新，不发送）
# =====================================================

app = get_app()


@app.get("/api/backfill/pending")
async def api_backfill_pending():
    """统计各启用频道的待补发缺口数（取最落后群的记录为基准）

    返回 {"ok": True, "total": int, "channels": {频道ID: {"name": str, "count": int}}}
    count 达到上限 1000 时表示"至少 1000 条"；统计失败记 -1"""
    channels = get_active_channels()

    active_groups = get_active_groups()

    if not channels or not active_groups:
        return {"ok": True, "total": 0, "channels": {}}

    last_messages = load_last_messages()

    result = {}
    total = 0

    for channel_id, channel_name in channels.items():

        channel_map = last_messages.get(channel_id)

        if not isinstance(channel_map, dict):
            channel_map = {"*": channel_map} if channel_map else {}
            last_messages[channel_id] = channel_map

        effective_ids = []

        for group in active_groups:

            last_id = (
                channel_map.get(group)
                or channel_map.get("*")
            )

            if last_id:
                effective_ids.append(last_id)

        if not effective_ids:
            result[channel_id] = {"name": channel_name, "count": 0}
            continue

        base_id = min(
            effective_ids,
            key=int
        )

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
                "统计待补发数量失败: %s",
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

    active_groups = get_active_groups()

    if not channels or not active_groups:
        return {"ok": True, "cleared": 0}

    last_messages = load_last_messages()

    cleared = 0

    for channel_id in channels:

        channel_map = last_messages.get(channel_id)

        if not isinstance(channel_map, dict):
            channel_map = {"*": channel_map} if channel_map else {}
            last_messages[channel_id] = channel_map

        latest = await fetch_channel_latest(
            channel_id
        )

        if not latest:
            continue

        changed = False

        for group in active_groups:
            if channel_map.get(group) != latest:
                channel_map[group] = latest
                changed = True

        if channel_map.get("*") != latest:
            channel_map["*"] = latest
            changed = True

        if changed:
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
