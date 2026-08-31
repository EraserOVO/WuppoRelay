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

        save_user_openid(
            event.author.user_openid
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

        await bot.send(
            event,
            "指令无效"
        )

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
