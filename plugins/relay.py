import asyncio

from nonebot import get_driver
from nonebot import logger
from nonebot import on_message
from nonebot.adapters import Event

from nonebot.adapters.discord import Bot as DiscordBot
from nonebot.adapters.discord.event import GuildMessageCreateEvent

from plugins.config import (
    get_active_channels,
    get_active_groups,
)
from plugins.history import (
    load_last_messages,
    save_last_messages,
)
from plugins.fetch import (
    normalize_event,
    build_parts,
    fetch_channel_latest,
    fetch_channel_after_count,
)
from plugins.sender import send_relay_message
from plugins.stats import record


relay = on_message(
    priority=20,
    block=False
)


@relay.handle()
async def handle(
    bot: DiscordBot,
    event: Event
):

    if not isinstance(
        bot,
        DiscordBot
    ):
        return


    if not isinstance(
        event,
        GuildMessageCreateEvent
    ):
        return


    channel_id = str(
        event.channel_id
    )


    channels = get_active_channels()


    if channel_id not in channels:
        return


    # 没有任何启用群时视为暂停转发，避免白下载媒体
    active_groups = get_active_groups()

    if not active_groups:
        return


    message_id = str(
        event.id
    )


    last_messages = load_last_messages()


    channel_map = last_messages.get(
        channel_id
    )

    if not isinstance(channel_map, dict):
        # 旧格式 {channel: id_str} 迁移为 {channel: {"*": id_str}}，
        # "*" 表示对所有群生效的兜底记录
        channel_map = {"*": channel_map} if channel_map else {}
        last_messages[channel_id] = channel_map


    # 按群分别判断是否跳过（A4：某个群发送失败不会拖累其他群的去重，
    # 重连补发时已成功的群跳过、失败的群重试）
    target_groups = []

    for group in active_groups:

        last_id = channel_map.get(
            group
        ) or channel_map.get(
            "*"
        )

        if (
            last_id is None
            or int(message_id) > int(last_id)
        ):
            target_groups.append(group)


    if not target_groups:

        logger.debug(
            "跳过旧消息: %s",
            message_id
        )

        return


    message = normalize_event(
        event,
        channels[channel_id]
    )


    text_parts, media_items, has_content = await build_parts(
        message
    )


    if not has_content:
        return


    ok_map = await send_relay_message(
        text_parts,
        media_items,
        target_groups
    )


    if not ok_map:
        # QQ Bot 未连接：本次消息未发送、不记录去重ID
        return


    if any(ok_map.values()):
        record(True)
    else:
        record(False)


    # 只给发送成功的群记录去重 ID（失败的群保留旧记录，
    # 重连补发时只补失败的群，已成功的群不会收到重复消息）
    changed = False

    for group, ok in ok_map.items():

        if ok:
            channel_map[group] = message_id
            changed = True


    if changed:

        save_last_messages(
            last_messages
        )

        logger.info(
            "记录Discord消息: %s %s",
            channel_id,
            message_id
        )

    else:

        logger.warning(
            "Discord消息发送失败，不记录去重ID（重连补发时会重试）: %s %s",
            channel_id,
            message_id
        )


# =====================================================
# 离线消息丢失提示（B5）
#
# 机器人每次连接时，对比每个启用频道的最新消息 ID 与已记录 ID，
# 发现缺口只输出警告日志（绝不补发任何消息），
# 提示离线期间可能丢失了多少条。
# =====================================================

driver = get_driver()


@driver.on_bot_connect
async def _check_missed_wrapper(bot):

    if isinstance(
        bot,
        DiscordBot
    ):
        asyncio.create_task(
            _report_missed()
        )


async def _report_missed():

    try:

        channels = get_active_channels()

        last_messages = load_last_messages()

        for channel_id, channel_name in channels.items():

            try:

                channel_map = last_messages.get(
                    channel_id
                )

                if isinstance(channel_map, dict):
                    ids = [
                        value
                        for value in channel_map.values()
                        if value
                    ]
                    last_id = max(ids) if ids else None
                else:
                    last_id = channel_map

                if not last_id:
                    continue

                latest = await fetch_channel_latest(
                    channel_id
                )

                if (
                    not latest
                    or int(latest) <= int(last_id)
                ):
                    continue

                count = await fetch_channel_after_count(
                    channel_id,
                    last_id
                )

                if not count:
                    continue

                if count > 100:
                    note = f"至少 {count - 1} 条"
                else:
                    note = f"{count} 条"

                logger.warning(
                    "频道[%s]离线期间可能丢失%s消息（最后记录=%s，最新=%s），未自动补发",
                    channel_name,
                    note,
                    last_id,
                    latest
                )

            except Exception:

                logger.exception(
                    "检查离线消息失败: %s",
                    channel_id
                )

    except Exception:

        logger.exception(
            "离线消息检查失败"
        )
