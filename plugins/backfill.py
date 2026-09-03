import asyncio

from nonebot import get_driver
from nonebot import logger
from nonebot.adapters import Bot

from nonebot.adapters.discord import Bot as DiscordBot

from plugins.config import (
    get_active_channels,
    get_groups_for_channel,
    get_backfill_limit,
    get_backfill_enabled,
    get_channel_filter,
)
from plugins.filter import check_message_filter
from plugins.history import (
    load_last_messages,
    update_last_messages,
)
from plugins.fetch import (
    build_parts,
    fetch_channel_latest,
    fetch_channel_messages_after,
)
from plugins.sender import log_partial_failure
from plugins.retry import (
    send_and_record,
    schedule_retry,
)
from plugins.stats import record
from plugins.dedup import (
    normalize_channel_map,
    select_target_groups,
    compute_base_id,
    apply_baseline,
    apply_success,
)


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

        if not channels:
            return

        limit = get_backfill_limit()

        for channel_id, channel_name in channels.items():

            # 每个频道独立路由：只补发该频道命中转发组勾选的群
            active_groups = get_groups_for_channel(channel_id)

            if not active_groups:
                continue

            try:

                await _backfill_channel(
                    channel_id,
                    channel_name,
                    active_groups,
                    limit,
                )

            except Exception:

                logger.exception(
                    "启动补发失败: {}",
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

    channel_map = normalize_channel_map(
        last_messages,
        channel_id,
    )

    # 各群有效 last_id 的最小值（群专属优先，缺失回退 "*" 兜底），
    # 避免古老的 "*" 兜底记录把补发起点拖得过旧；
    # 全无有效记录时返回 None（首次启用场景）
    base_id = compute_base_id(
        channel_map,
        active_groups,
    )

    # 无任何群的有效记录（首次启用）：只建基线（记录最新消息 ID），
    # 不补发历史，防止刷屏
    if base_id is None:

        latest = await fetch_channel_latest(
            channel_id
        )

        if latest:

            await update_last_messages(
                lambda last: apply_baseline(
                    normalize_channel_map(last, channel_id),
                    active_groups,
                    latest,
                )
            )

            logger.info(
                "启动补发: 频道[{}]无历史记录，已记录最新消息 {} 作为基线，不补发历史",
                channel_name,
                latest
            )

        return

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

        channel_map = normalize_channel_map(
            last_messages,
            channel_id,
        )

        target_groups = select_target_groups(
            channel_map,
            active_groups,
            message_id,
        )

        if not target_groups:
            continue

        # 频道消息筛选：被过滤的消息仍更新去重记录，
        # 避免下次补发反复重处理同一条消息
        msg_author_username = msg.get("author_username") or ""
        filter_config = get_channel_filter(channel_id)

        if not check_message_filter(
            filter_config,
            msg_author_username,
            msg.get("content") or "",
            msg.get("embeds"),
        ):
            logger.debug(
                "启动补发: 消息被筛选跳过: {} {} (author={})",
                channel_id,
                message_id,
                msg_author_username,
            )

            await update_last_messages(
                lambda last: apply_success(
                    normalize_channel_map(last, channel_id),
                    {g: True for g in target_groups},
                    message_id,
                )
            )

            continue

        msg["channel_name"] = channel_name

        text_parts, media_items, has_content = await build_parts(
            msg,
            source_label="自动补发来自",
        )

        if not has_content:
            continue

        ok_map = await send_and_record(
            channel_id,
            message_id,
            text_parts,
            media_items,
            target_groups,
        )

        if not ok_map:
            # QQ Bot 未连接：本次消息未发送、不记录去重ID，停止补发
            logger.warning(
                "启动补发: QQ未连接，补发中断: {} {}",
                channel_id,
                message_id
            )
            return

        # 多群部分失败时明确列出失败群（全成功/全失败由后续日志负责）
        log_partial_failure(
            ok_map,
            channel_id,
            message_id,
        )

        if any(ok_map.values()):
            record(True)
        else:
            record(False)

        failed_groups = [
            group
            for group, ok in ok_map.items()
            if not ok
        ]

        if failed_groups:
            # 补发中也失败：登记延迟重试（其他路径送达时 purge 会清欠账）
            schedule_retry(
                channel_id,
                message_id,
                msg,
                failed_groups,
                source_label="自动补发来自",
            )

        if any(ok_map.values()):

            logger.info(
                "启动补发: 记录Discord消息 {} {}",
                channel_id,
                message_id
            )

        else:

            logger.warning(
                "启动补发: 发送失败，不记录去重ID（延迟重试/下次补发会处理）: {} {}",
                channel_id,
                message_id
            )
