import os
import time

import httpx

from nonebot import logger
from nonebot import on_message
from nonebot import get_driver
from nonebot.adapters import Bot, Event

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    GroupMessageCreateEvent,
)

from plugins.config import get_active_user_openids
from plugins.json_io import (
    load_json,
    atomic_write_json,
)


# =====================================================
# 私聊管理命令
#
# 白名单内的 QQ 用户私聊 Bot，通过管理面板的 8090 API
# 执行管理操作（状态/重启/模式/列表/补发），不在本进程
# 内重复实现管理逻辑；面板是唯一的管理入口。
#
# 无前缀触发：私聊直接发送命令词（如 status、backfill on），
# 帮助命令为 list；relay 消息由 plugins/command.py 处理。
# 面板只绑定 127.0.0.1，trust_env=False 避免走 HTTP_PROXY
# （代理仅用于 Discord 访问）。
# =====================================================

# 与管理面板 PANEL_PORT（panel/管理面板.pyw）保持一致
PANEL_API_BASE = "http://127.0.0.1:8090"

_panel_client = httpx.AsyncClient(
    base_url=PANEL_API_BASE,
    timeout=10.0,
    trust_env=False,
)

MODE_LABELS = {
    "test": "测试模式",
    "forward": "转发模式",
    "custom": "自定义模式",
}

# =====================================================
# restart 跨进程确认
#
# restart 由管理面板结束本进程再拉起新进程，新进程对
# "谁触发了重启"一无所知。因此触发 restart 前先把目标
# openid 写入标记文件，新进程 QQ 网关重连成功后据此
# 私聊回复"重启完毕"，并清除标记。
# =====================================================

RESTART_PENDING_FILE = "data/restart_pending.json"
RESTART_CONFIRM_MAX_AGE = 300  # 标记超过 5 分钟视为过期，避免误发


def _write_restart_pending(openid):
    atomic_write_json(
        RESTART_PENDING_FILE,
        {
            "openid": openid,
            "time": int(time.time()),
        },
        indent=2,
    )


def _clear_restart_pending():
    try:
        os.remove(RESTART_PENDING_FILE)
    except OSError:
        pass


@get_driver().on_bot_connect
async def _confirm_restart(bot: Bot):
    """QQ 网关重连成功后，向触发 restart 的用户回复"重启完毕" """
    if not isinstance(bot, QQBot):
        return
    data = load_json(RESTART_PENDING_FILE, default=None)
    if not isinstance(data, dict) or not data.get("openid"):
        return
    # 过期的标记（如重启失败遗留）直接清除，不误发
    created = data.get("time")
    if (
        not isinstance(created, (int, float))
        or time.time() - created > RESTART_CONFIRM_MAX_AGE
    ):
        _clear_restart_pending()
        return
    try:
        # 与 adapter 回复路径一致，用 author.id 作为 send_to_c2c 的 openid
        await bot.send_to_c2c(
            openid=data["openid"],
            message="重启完毕",
        )
        logger.info(
            "已向 {} 发送重启完毕",
            data["openid"],
        )
    except Exception:
        logger.exception(
            "发送重启完毕失败: {}",
            data["openid"],
        )
    finally:
        _clear_restart_pending()


manage_command = on_message(
    priority=20,
    block=False
)


def _parse_response(resp):
    """把面板响应统一成 dict；非 2xx 也返回内容，便于展示面板的 msg"""
    try:
        data = resp.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if resp.status_code >= 400:
        data.setdefault("ok", False)
        data.setdefault("msg", f"面板返回 HTTP {resp.status_code}")
    return data


async def _panel_get(path):
    resp = await _panel_client.get(path)
    return _parse_response(resp)


async def _panel_post(path, body=None):
    resp = await _panel_client.post(path, json=body)
    return _parse_response(resp)


def _help_text():
    return (
        "【私聊命令】\n"
        "status 机器人状态\n"
        "restart 重启机器人\n"
        "mode <forward/test> 转发模式/测试模式\n"
        "groups 群列表\n"
        "channels 频道列表\n"
        "users 权限列表\n"
        "backfill 补发状态\n"
        "backfill <on/off/run/clear/refresh> 开/关/触发/清空/刷新\n"
        "register {QQ号} {昵称} 用户私聊提交注册申请\n"
        "list 查看命令清单"
    )


def _group_help_text():
    return (
        "【群聊命令】\n"
        "register-group {群号} {群名称} 群内提交群注册申请\n"
        "转发 relay 转发你最近一条消息到其他QQ群"
    )


async def _cmd_status():
    data = await _panel_get("/api/status")
    settings = await _panel_get("/api/settings")

    lines = ["【机器人状态】"]

    if data.get("running"):
        lines.append("运行中 PID:" + str(data.get("pid") or "?"))
    else:
        lines.append("已停止")

    stats = data.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    lines.append(
        "今日转发{}条 累计{}条 失败{}条".format(
            stats.get("today_forwarded", 0),
            stats.get("total_forwarded", 0),
            stats.get("total_failed", 0),
        )
    )

    lines.append("")

    mode = data.get("mode")
    lines.append(
        "工作模式：" + (MODE_LABELS.get(mode, mode) if mode else "未知")
    )

    enabled = settings.get("backfill_enabled")
    if enabled is None:
        enabled = True
    lines.append("离线补发：" + ("开启" if enabled else "关闭"))
    lines.append("开机自启：" + ("开启" if data.get("autostart") else "关闭"))

    return "\n".join(lines)


async def _cmd_mode(args):
    mode = args[0] if args else ""

    # 私聊仅允许切换转发/测试两种模式；自定义模式由管理面板设置
    if mode not in ("test", "forward"):
        return "用法: mode <forward/test>（转发/测试）"

    data = await _panel_post(
        "/api/mode/apply",
        {"mode": mode},
    )
    if data.get("ok"):
        return "已切换为" + MODE_LABELS[mode]
    return data.get("msg") or "切换模式失败"


async def _cmd_list(kind):
    """查询群/频道/用户列表（复用面板 /api/settings 的配置与 getter）"""
    if kind == "qq_group_openids":
        title, id_key, desc_key = "群列表", "openid", "name"
    elif kind == "discord_channels":
        title, id_key, desc_key = "频道列表", "id", "name"
    else:
        title, id_key, desc_key = "权限列表", "openid", "name"

    settings = await _panel_get("/api/settings")
    items = settings.get(kind) or []

    lines = [f"【{title}】"]

    for item in items:
        if not isinstance(item, dict):
            continue
        flag = "（启用）" if item.get("enabled") else "（未启用）"
        desc = str(item.get(desc_key) or "").strip()
        if not desc:
            # 群/用户条目未注册时无 name，回退显示原 remark（如"自动发现，点击启用"）
            desc = str(item.get("remark") or "").strip()
        ident = str(item.get(id_key) or "?")
        text = flag + (desc + " " if desc else "") + ident
        if item.get("is_test"):
            text += "（测试）"
        lines.append(text)

    lines.append(f"（总计{len(items)}个）")

    return "\n".join(lines)


async def _cmd_backfill(args):
    arg = args[0] if args else ""

    if not arg:
        # 查询：总开关 + 单次补发上限 + 待补发缺口数
        settings = await _panel_get("/api/settings")

        enabled = settings.get("backfill_enabled")
        if enabled is None:
            enabled = True

        pending = await _panel_get("/api/backfill/pending")
        if isinstance(pending, dict) and pending.get("ok"):
            pending_text = str(pending.get("total", 0))
        else:
            pending_text = "未知"

        return (
            "【离线补发状态】\n"
            "当前"
            + ("开启" if enabled else "关闭")
            + "\n"
            "单次补发上限："
            + str(settings.get("backfill_limit", 10))
            + "条/频道\n"
            "待补发：" + pending_text
        )

    if arg in ("on", "off"):
        value = arg == "on"
        # 字段级接口：只提交 backfill_enabled，不再 GET 全量配置后
        # 回传，避免覆盖面板刚保存的其他字段；已处于目标状态时
        # 服务端不写盘并返回 changed=false，直接告知用户
        data = await _panel_post(
            "/api/settings/backfill-toggle",
            {"backfill_enabled": value},
        )
        if data.get("ok"):
            if data.get("changed") is False:
                return (
                    "离线补发已处于"
                    + ("开启" if value else "关闭")
                    + "状态"
                )
            return "已" + ("开启" if value else "关闭") + "离线补发"
        return data.get("msg") or "设置失败"

    if arg == "run":
        # 先查待补发缺口，无消息时直接告知，避免空触发
        pending = await _panel_get("/api/backfill/pending")
        if pending.get("ok") and not pending.get("total"):
            return "当前无可补发的消息"
        data = await _panel_post("/api/backfill/run")
        if data.get("ok"):
            return "补发已触发"
        return data.get("msg") or "补发触发失败"

    if arg == "clear":
        data = await _panel_post("/api/backfill/clear")
        if data.get("ok"):
            cleared = data.get("cleared", 0)
            # 记录都已推进到最新，无待清除内容
            if not cleared:
                return "当前无可清空的待补发"
            return (
                "已清空待补发（"
                + str(cleared)
                + " 个频道记录推进到最新）"
            )
        return data.get("msg") or "清空失败"

    if arg == "refresh":
        # 立即读取一次 Discord 实时状态（各频道待补发缺口）
        pending = await _panel_get("/api/backfill/pending")
        if not pending.get("ok"):
            return pending.get("msg") or "刷新失败"
        lines = ["【Discord 实时状态】"]
        channels = pending.get("channels") or {}
        lines.append("待补发合计：" + str(pending.get("total", 0)) + " 条")
        for info in list(channels.values())[:15]:
            if not isinstance(info, dict):
                continue
            count = info.get("count")
            name = info.get("name")
            if count is None or count < 0:
                lines.append(f"{name or '?'}：统计失败")
            elif count:
                lines.append(f"{name or '?'}：{count} 条")
        if len(channels) > 15:
            lines.append("…等共 " + str(len(channels)) + " 个频道")
        return "\n".join(lines)

    return "用法: backfill（查询）/ on/off / run / clear / refresh"


async def _dispatch(cmd, args):
    if cmd == "list":
        return _help_text()

    if cmd == "status":
        return await _cmd_status()

    if cmd == "mode":
        return await _cmd_mode(args)

    if cmd == "groups":
        return await _cmd_list("qq_group_openids")

    if cmd == "channels":
        return await _cmd_list("discord_channels")

    if cmd == "users":
        return await _cmd_list("qq_user_openids")

    if cmd == "backfill":
        return await _cmd_backfill(args)

    return "未知命令，输入 list 查看可用命令"


@manage_command.handle()
async def handle(
    bot: Bot,
    event: Event
):

    if not isinstance(
        bot,
        QQBot
    ):
        return

    # 群聊仅支持 list（查看群聊命令清单，不做白名单校验）；
    # 其余群消息保持静默，register-group 由 plugins/registration.py 处理
    if isinstance(
        event,
        GroupMessageCreateEvent
    ):
        message = event.get_plaintext().strip()

        if message == "list":
            await bot.send(
                event,
                _group_help_text()
            )

        return

    if not isinstance(
        event,
        C2CMessageCreateEvent
    ):
        return

    message = event.get_plaintext()

    if not message:
        return

    parts = message.split()

    if not parts:
        return

    cmd = parts[0]

    args = parts[1:]

    # relay 私聊转发由 plugins/command.py 处理
    if cmd == "relay":
        return

    # register / register-group 注册指令由 plugins/registration.py 处理
    # （注册对未授权用户开放，不做白名单校验，避免这里回复"未授权"
    # 造成双重回复）；中文"注册"已删除，按未知命令正常回复
    if cmd in ("register", "register-group"):
        return

    # 白名单校验：与 relay 命令一致，仅允许管理面板勾选放行的用户
    if event.author.user_openid not in get_active_user_openids():

        logger.info(
            "拒绝未授权用户私聊命令: {}",
            event.author.user_openid
        )

        await bot.send(
            event,
            "未授权使用管理命令（请在管理面板勾选放行）"
        )

        return

    logger.info(
        "收到私聊命令: {}",
        message
    )

    # restart 由管理面板结束本进程（taskkill 进程树），
    # 确认回复必须先于进程退出发出；同时写标记文件，
    # 供重启后的新进程 QQ 重连时回复"重启完毕"
    if cmd == "restart":

        await bot.send(
            event,
            "正在重启机器人..."
        )

        _write_restart_pending(event.author.id)

        try:

            await _panel_post(
                "/api/bot/restart"
            )

        except Exception as exc:

            logger.warning(
                "调用面板 restart 失败: {}",
                exc
            )

            # 重启未发生，清除标记避免之后误发"重启完毕"
            _clear_restart_pending()

        return

    try:

        reply = await _dispatch(
            cmd,
            args
        )

    except httpx.HTTPError:

        logger.warning(
            "管理面板不可达: {}",
            message
        )

        reply = "管理面板未运行，无法执行管理操作（请先启动管理面板）"

    except Exception:

        logger.exception(
            "QQ管理命令执行失败: {}",
            message
        )

        reply = "管理命令执行失败（请查看日志）"

    if reply:

        await bot.send(
            event,
            reply
        )
