import datetime
import time

from nonebot import logger
from nonebot import on_message
from nonebot import on_notice
from nonebot.adapters import Bot, Event

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    GroupAddRobotEvent,
    GroupMessageCreateEvent,
)

from plugins import identities
from plugins.config import (
    get_active_groups,
    get_all_channels,
    get_active_user_openids,
    get_qq_group_entries,
    get_qq_fwd_select_timeout,
)
from plugins.fetch import (
    is_discord_url,
    fetch_message,
    build_parts,
)
from plugins.media import make_text
from plugins.sender import send_relay_message
from plugins.json_io import (
    load_json,
    atomic_write_json,
)


relay_command = on_message(
    priority=14,
    block=False
)


# =====================================================
# group_openid / 用户 openid 自动发现
#
# 机器人被拉进群 / 收到群消息时，自动把群 openid 打印到控制台
# 并写入 data/qq_group_openids.json；收到私聊消息时，把用户
# openid 写入 data/qq_user_openids.json。面板同步后勾选放行，
# 未放行的用户触发 relay 会被拒绝。
# =====================================================

OPENID_FILE = "data/qq_group_openids.json"
USER_OPENID_FILE = "data/qq_user_openids.json"


def save_group_openid(openid):

    data = load_json(
        OPENID_FILE,
        default={}
    )

    if not isinstance(data, dict):

        data = {}


    openids = data.setdefault(
        "group_openids",
        []
    )

    if openid not in openids:

        openids.append(openid)

        atomic_write_json(
            OPENID_FILE,
            data,
            indent=4
        )

    logger.info(
        "[QQ group_openid] {}",
        openid
    )


def save_user_openid(openid):

    data = load_json(
        USER_OPENID_FILE,
        default={}
    )

    if not isinstance(data, dict):

        data = {}


    openids = data.setdefault(
        "user_openids",
        []
    )

    if openid not in openids:

        openids.append(openid)

        atomic_write_json(
            USER_OPENID_FILE,
            data,
            indent=4
        )

    logger.info(
        "[QQ user_openid] {}",
        openid
    )


# =====================================================
# OpenID 身份记录
#
# 在自动发现 openid 的同时，把昵称/群名、最后活动时间记录到
# data/qq_identities.json（独立于白名单的身份资料库）。名称只
# 用于面板展示识别，绝不参与放行判断（qq_user_openids 仍是唯一
# 权限来源）。新发现的用户只记录身份，不会自动加入白名单。
#
# 昵称取事件自带（FriendAuthor/GroupMemberAuthor.username），
# 缺失时保持「未命名」，由面板兜底显示并允许手动备注。
# QQ 官方 API 不提供群名/昵称查询接口，无法主动补全。
# =====================================================

def _event_ts(event):
    """取事件时间（秒）；QQ 时间戳可能是 ms / datetime / str"""
    ts = getattr(event, "timestamp", None)
    if isinstance(ts, datetime.datetime):
        return ts.timestamp()
    if isinstance(ts, (int, float)):
        return ts / 1000.0 if ts > 1e12 else ts
    if isinstance(ts, str):
        try:
            v = float(ts)
            return v / 1000.0 if v > 1e12 else v
        except ValueError:
            pass
    return time.time()


openid_logger = on_message(
    priority=1,
    block=False
)


@openid_logger.handle()
async def log_group_message_openid(
    bot: Bot,
    event: Event
):

    if not isinstance(
        bot,
        QQBot
    ):
        return

    if isinstance(
        event,
        C2CMessageCreateEvent
    ):

        openid = event.author.user_openid

        save_user_openid(
            openid
        )

        # 记录身份：昵称取事件自带，缺失时保持未命名
        username = str(
            getattr(
                event.author,
                "username",
                ""
            ) or ""
        ).strip()

        identities.record_user_identity(
            openid,
            nickname=username or None,
            last_active=_event_ts(event),
        )

        return

    if not isinstance(
        event,
        GroupMessageCreateEvent
    ):
        return

    save_group_openid(
        event.group_openid
    )

    identities.record_group_identity(
        event.group_openid,
        last_active=_event_ts(event),
    )


group_notice = on_notice(
    priority=1,
    block=False
)


@group_notice.handle()
async def log_group_add_openid(
    bot: Bot,
    event: Event
):

    if not isinstance(
        bot,
        QQBot
    ):
        return

    if not isinstance(
        event,
        GroupAddRobotEvent
    ):
        return

    save_group_openid(
        event.group_openid
    )

    identities.record_group_identity(
        event.group_openid,
        last_active=_event_ts(event),
    )


# =====================================================
# 私聊 relay 命令（两步式：先指定目标，再发送下一条内容）
#
#   relay-list            列出可转发目标群（全部已注册且 enabled，私聊不做测试/正式隔离）
#   relay {序号}          选择第 N 个群，静默等待下一条普通消息转发
#   relay all             选择全部可用群，静默等待下一条普通消息转发
#   relay / relay abc / relay 999 → 无法找到对应的转发群
#
# 下一条普通消息才执行转发；成功/失败回复结果；超时（qq_fwd_select_timeout）
# 只清 pending 不回复。交互状态绑定 user_openid，避免多人串台。
# 私聊专属：下一条消息若是 Discord 链接，走现有 fetch_message + build_parts 拉取机制。
#
# 与 manage.py（priority=20）的关系：本 matcher priority=15 先执行；凡是被本段
# 消费的消息（relay 指令、待转发的下一条内容）都 stop_propagation，避免 manage
# 再把同一消息当私聊命令回复「未知命令」造成双回复。
# =====================================================

# 私聊两步式 pending：user_openid -> {"created_ts": float, "targets": "all" | [openid,...]}
_c2c_pending = {}


def _stop_propagation():
    """阻止当前事件继续传播到更低优先级 matcher（防止 manage.py 双回复）。

    测试直接调用 handler 时无 current_matcher 上下文，静默忽略。"""
    try:
        from nonebot.internal.matcher import current_matcher
        current_matcher.get().stop_propagation()
    except Exception:
        pass


def _c2c_enabled_groups():
    """私聊可选目标：全部已注册且 enabled 的群（不受测试/正式隔离）"""
    return [
        e for e in get_qq_group_entries()
        if e.get("enabled") and e.get("openid")
    ]


def _c2c_menu_text():
    groups = _c2c_enabled_groups()
    lines = ["【Relay群列表】"]
    if not groups:
        lines.append("当前没有可用的QQ接收群（请在管理面板勾选）")
    for i, g in enumerate(groups, start=1):
        name = str(g.get("name") or g.get("remark") or "").strip() or g.get("openid")
        lines.append(f"{i}. {name}")
    return "\n".join(lines)


def set_c2c_pending(user_openid, targets):
    _c2c_pending[user_openid] = {
        "created_ts": time.time(),
        "targets": targets,
    }


def get_c2c_pending(user_openid):
    return _c2c_pending.get(user_openid)


def clear_c2c_pending(user_openid):
    _c2c_pending.pop(user_openid, None)


def expire_c2c_pendings(now_ts=None, skip=None):
    """清除所有已超时的私聊 pending（静默，不回复）；返回清除的用户列表。

    skip：跳过不清除的用户（当前用户由 _consume_next 内联处理，
    避免其过期 pending 被提前删掉后下一条消息落入 manage.py 被误回）。"""
    now = now_ts if now_ts is not None else time.time()
    timeout = get_qq_fwd_select_timeout()
    expired = [
        u for u, e in _c2c_pending.items()
        if u != skip and now - e["created_ts"] > timeout
    ]
    for u in expired:
        clear_c2c_pending(u)
    return expired


@relay_command.handle()
async def handle(
    bot: QQBot,
    event: Event
):

    if not isinstance(
        bot,
        QQBot
    ):

        return

    if not isinstance(
        event,
        C2CMessageCreateEvent
    ):

        return

    message = event.get_plaintext()

    if not message:
        # 空文本（如纯图片/表情）不打扰
        return

    user = event.author.user_openid
    stripped = message.strip()

    # 懒清扫：清掉其他用户已超时的 pending（当前用户的过期由 _consume_next 内联处理）
    expire_c2c_pendings(time.time(), skip=user)

    # ---- relay 指令 ----
    if (
        stripped == "relay-list"
        or stripped == "relay"
        or stripped.startswith("relay ")
    ):
        await _handle_relay_cmd(bot, event, stripped, user)
        return

    # ---- 下一条消息消费（有 pending 时）----
    entry = get_c2c_pending(user)
    if entry is not None:
        await _consume_next(bot, event, message, entry, user)
        return

    # 无 pending、非 relay：交给 manage.py 正常处理


async def _handle_relay_cmd(bot, event, stripped, user):

    # 白名单校验：私聊 relay 仅允许管理面板中勾选放行的用户使用
    if user not in get_active_user_openids():

        logger.info(
            "拒绝未授权用户relay: {}",
            user
        )

        await bot.send(
            event,
            "未授权使用relay命令（请在管理面板勾选放行）"
        )

        _stop_propagation()
        return

    # relay-list：列出可转发目标群
    if stripped == "relay-list":

        await bot.send(
            event,
            _c2c_menu_text()
        )

        _stop_propagation()
        return

    arg = stripped[6:].strip() if stripped.startswith("relay ") else ""

    # 裸 relay / 非数字 / 越界 → 无法找到对应的转发群
    if arg == "":

        await bot.send(
            event,
            "无法找到对应的转发群"
        )

        _stop_propagation()
        return

    if arg == "all":

        groups = _c2c_enabled_groups()

        if not groups:

            await bot.send(
                event,
                "当前没有可用的QQ接收群（请在管理面板勾选）"
            )

            _stop_propagation()
            return

        set_c2c_pending(user, "all")
        _stop_propagation()
        return

    if arg.isdigit():

        groups = _c2c_enabled_groups()
        idx = int(arg)

        if idx < 1 or idx > len(groups):

            await bot.send(
                event,
                "无法找到对应的转发群"
            )

            _stop_propagation()
            return

        set_c2c_pending(user, [str(groups[idx - 1]["openid"])])
        _stop_propagation()
        return

    # relay <其他内容>：不识别
    await bot.send(
        event,
        "无法找到对应的转发群"
    )

    _stop_propagation()


async def _consume_next(bot, event, message, entry, user):

    # 超时：只清 pending，不回复、不转发
    timeout = get_qq_fwd_select_timeout()
    if time.time() - entry["created_ts"] > timeout:

        clear_c2c_pending(user)
        _stop_propagation()
        return

    clear_c2c_pending(user)

    # 目标群
    if entry["targets"] == "all":
        groups = get_active_groups()
    else:
        groups = entry["targets"]

    if not groups:

        await bot.send(
            event,
            "转发失败"
        )

        _stop_propagation()
        return

    # 内容：DC 链接走现有 Discord 消息拉取机制（仅私聊生效）
    if is_discord_url(message):

        logger.info(
            "检测到Discord消息链接"
        )

        msg, fetch_err = await fetch_message(
            message
        )

        if msg is None:

            await bot.send(
                event,
                "获取Discord消息失败"
                + (
                    f"（{fetch_err}）"
                    if fetch_err
                    else ""
                )
            )

            _stop_propagation()
            return

        # 频道名：面板配置名优先，未配置时由 build_parts 兜底查真实名
        msg["channel_name"] = get_all_channels().get(
            msg["channel_id"]
        )

        text_parts, media_items, _ = await build_parts(
            msg,
            source_label="手动转发来自",
        )

    else:

        text_parts = [make_text(message)]
        media_items = []

    ok_map = await send_relay_message(
        text_parts,
        media_items,
        groups=groups,
    )

    if not ok_map:

        await bot.send(
            event,
            "转发失败"
        )

    elif all(ok_map.values()):

        await bot.send(
            event,
            "转发成功"
        )

    else:

        await bot.send(
            event,
            "转发失败"
        )

    _stop_propagation()


# =====================================================
# 私聊 pending 超时采用懒清扫（见 handle() 顶部的 expire_c2c_pendings）：
# 每次收到 C2C 消息时顺带清除其他用户已超时的 pending；当前用户的过期
# 由 _consume_next 内联判定（只清 pending，不回复）。不启用后台循环，
# 避免模块级 get_driver 依赖（管理面板/测试直接导入本模块时 nonebot 未初始化）。
# =====================================================
