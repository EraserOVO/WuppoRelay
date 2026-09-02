from nonebot import logger
from nonebot import on_message
from nonebot.adapters import Event

from nonebot.adapters.discord import Bot as DiscordBot
from nonebot.adapters.discord.event import GuildMessageCreateEvent

from plugins.config import (
    get_active_channels,
    get_active_groups,
    get_channel_filter,
)
from plugins.filter import check_message_filter
from plugins.history import (
    load_last_messages,
    save_last_messages,
)
from plugins.fetch import (
    normalize_event,
    build_parts,
)
from plugins.sender import send_relay_message
from plugins.stats import record
from plugins.dedup import (
    normalize_channel_map,
    select_target_groups,
    apply_success,
)


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

    channel_map = normalize_channel_map(
        last_messages,
        channel_id,
    )

    # 按群分别判断是否跳过（A4：某个群发送失败不会拖累其他群的去重，
    # 重连补发时已成功的群跳过、失败的群重试）
    target_groups = select_target_groups(
        channel_map,
        active_groups,
        message_id,
    )


    if not target_groups:

        logger.debug(
            "跳过旧消息: {}",
            message_id
        )

        return

    # 频道消息筛选：被过滤的消息仍更新去重记录，
    # 避免补发时反复重处理同一条消息
    author_username = getattr(event.author, "username", "") if getattr(event, "author", None) else ""
    filter_config = get_channel_filter(channel_id)

    if not check_message_filter(
        filter_config,
        author_username,
        getattr(event, "content", "") or "",
        getattr(event, "embeds", None),
    ):
        logger.debug(
            "消息被筛选跳过: {} {} (author={})",
            channel_id,
            message_id,
            author_username,
        )

        apply_success(
            channel_map,
            {g: True for g in target_groups},
            message_id,
        )
        save_last_messages(last_messages)

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
    if apply_success(
        channel_map,
        ok_map,
        message_id,
    ):

        save_last_messages(
            last_messages
        )

        logger.info(
            "记录Discord消息: {} {}",
            channel_id,
            message_id
        )

    else:

        logger.warning(
            "Discord消息发送失败，不记录去重ID（重连补发时会重试）: {} {}",
            channel_id,
            message_id
        )


# =====================================================
# 离线消息丢失提示（B5）已移除：
# 启动历史补发由 plugins/backfill.py 承担（连接后逐条补发缺口消息），
# 不再只告警不补发。
# =====================================================
