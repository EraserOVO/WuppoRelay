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
    get_allowed_users,
    DISCORD_CHANNELS,
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
# group_openid 自动发现
#
# 机器人被拉进群 / 收到群消息时，自动把群 openid
# 打印到控制台并写入 data/qq_group_openids.json
# =====================================================

OPENID_FILE = "data/qq_group_openids.json"


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
        "[QQ group_openid] %s",
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


    if not message.startswith(
        "relay "
    ):

        return


    content = message[6:].strip()


    if not content:

        return


    # 私聊转发白名单（settings.json 的 allowed_users，留空 = 不限制）
    allowed = get_allowed_users()

    if allowed:

        user_openid = getattr(
            event,
            "user_openid",
            ""
        ) or ""

        if user_openid not in allowed:

            await bot.send(
                event,
                "你没有使用转发功能的权限"
            )

            return


    logger.info(
        "收到relay命令: %s",
        content
    )


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


        message = await fetch_message(
            content
        )


        if message is None:

            await bot.send(
                event,
                "获取Discord消息失败"
            )

            return


        message["channel_name"] = DISCORD_CHANNELS.get(
            message["channel_id"],
            message["channel_id"]
        )


        text_parts, media_items, _ = await build_parts(
            message
        )


    # 普通文字模式

    else:


        text_parts = [

            make_text(
                "手动转发的消息：\n"
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
