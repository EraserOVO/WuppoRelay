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
    priority=20,
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
# 私聊 relay 命令
# =====================================================


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


    if not message.startswith(
        "relay "
    ):

        # 非 relay 私聊消息由 plugins/manage.py 统一处理
        # （命令识别与未知命令回复），这里保持静默
        return


    content = message[6:].strip()


    if not content:

        return


    logger.info(
        "收到relay命令: {}",
        content
    )


    # 白名单校验：仅允许管理面板中勾选放行的用户使用 relay
    if event.author.user_openid not in get_active_user_openids():

        logger.info(
            "拒绝未授权用户relay: {}",
            event.author.user_openid
        )

        await bot.send(
            event,
            "未授权使用relay命令（请在管理面板勾选放行）"
        )

        return


    groups = get_active_groups()

    if not groups:

        await bot.send(
            event,
            "当前没有启用的QQ接收群（请在管理面板勾选）"
        )

        return


    # Discord链接模式

    if is_discord_url(content):

        logger.info(
            "检测到Discord消息链接"
        )


        message, fetch_err = await fetch_message(
            content
        )


        if message is None:

            await bot.send(
                event,
                "获取Discord消息失败"
                + (
                    f"（{fetch_err}）"
                    if fetch_err
                    else ""
                )
            )

            return


        # 频道名：面板配置名优先，未配置时由 build_parts 兜底查真实名
        message["channel_name"] = get_all_channels().get(
            message["channel_id"]
        )


        text_parts, media_items, _ = await build_parts(
            message,
            source_label="手动转发来自",
        )


    # 普通文字模式

    else:


        text_parts = [

            make_text(
                "手动发送的消息：\n"
                + content
            )

        ]

        media_items = []


    ok_map = await send_relay_message(
        text_parts,
        media_items
    )


    if not ok_map:

        await bot.send(
            event,
            "转发到QQ群失败（请查看日志）"
        )

    elif all(ok_map.values()):

        await bot.send(
            event,
            "已转发到QQ群"
        )

    else:

        await bot.send(
            event,
            "部分群转发失败（请查看日志）"
        )
