import asyncio

from nonebot import get_driver
from nonebot import logger
from nonebot.adapters import Bot

from nonebot.adapters.discord import Bot as DiscordBot

from plugins.config import (
    get_active_channels,
    get_active_groups,
    get_backfill_limit,
    get_backfill_enabled,
)
from plugins.history import (
    load_last_messages,
    save_last_messages,
)
from plugins.fetch import (
    build_parts,
    fetch_channel_latest,
    fetch_channel_messages_after,
)
from plugins.sender import send_relay_message
from plugins.stats import record


# =====================================================
# 启动历史补发
#
# Discord Bot 每次连接后，读取各启用频道在离线期间新增的
# 尚未转发过的消息，按时间正序逐条转发到缺口群。
# 与实时转发共用 history.py 的去重记录：发送成功即写记录，
# 实时路径先到则补发重读记录跳过，避免同一消息重复发送。
# 每个频道每次最多补发 get_backfill_limit() 条（默认 10），
# 防止离线过久时一次性刷屏。
# =====================================================

_backfill_running = False

driver = get_driver()


@driver.on_bot_connect
async def _backfill_wrapper(bot: Bot):

    if isinstance(
        bot,
        DiscordBot
    ):
        asyncio.create_task(
            _backfill_missed()
        )


async def _backfill_missed():

    global _backfill_running

    if _backfill_running:
        return

    if not get_backfill_enabled():
        logger.info(
            "启动补发: 补发功能已关闭（backfill_enabled=false），跳过"
        )
        return

    _backfill_running = True

    try:

        channels = get_active_channels()

        active_groups = get_active_groups()

        if not channels or not active_groups:
            return

        limit = get_backfill_limit()

        for channel_id, channel_name in channels.items():

            try:

                await _backfill_channel(
                    channel_id,
                    channel_name,
                    active_groups,
                    limit,
                )

            except Exception:

                logger.exception(
                    "启动补发失败: %s",
                    channel_id
                )

    except Exception:

        logger.exception(
            "启动补发失败"
        )

    finally:

        _backfill_running = False


async def _backfill_channel(channel_id, channel_name, active_groups, limit):

    last_messages = load_last_messages()

    channel_map = last_messages.get(
        channel_id
    )

    if not isinstance(channel_map, dict):
        # 旧格式 {channel: id_str} 迁移为 {channel: {"*": id_str}}
        channel_map = {"*": channel_map} if channel_map else {}
        last_messages[channel_id] = channel_map

    # 各群有效记录（群专属优先，缺失回退 "*" 兜底），
    # 不再直接取 channel_map 所有 value 的最小值，
    # 避免古老的 "*" 兜底记录把补发起点拖得过旧
    effective_ids = []

    for group in active_groups:

        last_id = (
            channel_map.get(group)
            or channel_map.get("*")
        )

        if last_id:
            effective_ids.append(last_id)

    # 无任何群的有效记录（首次启用）：只建基线（记录最新消息 ID），
    # 不补发历史，防止刷屏
    if not effective_ids:

        latest = await fetch_channel_latest(
            channel_id
        )

        if latest:

            for group in active_groups:
                channel_map[group] = latest

            save_last_messages(
                last_messages
            )

            logger.info(
                "启动补发: 频道[{}]无历史记录，已记录最新消息 {} 作为基线，不补发历史",
                channel_name,
                latest
            )

        return

    # 以最落后的群记录为起点，确保每个群的缺口都不漏
    base_id = min(
        effective_ids,
        key=int
    )

    messages = await fetch_channel_messages_after(
        channel_id,
        base_id,
        limit,
    )

    if not messages:
        return

    logger.info(
        "启动补发: 频道[{}]本次补发 {} 条缺口消息（旧优先分批，最多 {} 条）",
        channel_name,
        len(messages),
        limit
    )

    # fetch 已返回旧→新，按时间正序逐条转发
    for msg in messages:

        message_id = msg.get("message_id")

        if not message_id:
            continue

        # 逐条前重读最新记录，实时转发已处理的群自动跳过
        last_messages = load_last_messages()

        channel_map = last_messages.get(
            channel_id
        )

        if not isinstance(channel_map, dict):
            channel_map = {"*": channel_map} if channel_map else {}
            last_messages[channel_id] = channel_map

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
            continue

        msg["channel_name"] = channel_name

        text_parts, media_items, has_content = await build_parts(
            msg,
            source_label="自动补发来自",
        )

        if not has_content:
            continue

        ok_map = await send_relay_message(
            text_parts,
            media_items,
            target_groups
        )

        if not ok_map:
            # QQ Bot 未连接：本次消息未发送、不记录去重ID，停止补发
            logger.warning(
                "启动补发: QQ未连接，补发中断: {} {}",
                channel_id,
                message_id
            )
            return

        if any(ok_map.values()):
            record(True)
        else:
            record(False)

        # 只给发送成功的群记录去重 ID（失败的群保留旧记录，
        # 下次连接补发时只会补失败的群）
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
                "启动补发: 记录Discord消息 {} {}",
                channel_id,
                message_id
            )

        else:

            logger.warning(
                "启动补发: 发送失败，不记录去重ID: {} {}",
                channel_id,
                message_id
            )
