import asyncio
import time

from nonebot import logger

from plugins.dedup import (
    apply_success,
    normalize_channel_map,
)
from plugins.fetch import build_parts
from plugins.history import (
    load_last_messages,
    update_last_messages,
)
from plugins.sender import send_relay_message


# =====================================================
# 转发失败延迟重试（欠账队列 + 在途注册表）
#
# 目标：发送失败的消息不再因后续消息成功而永久丢失。
#   - pending 队列记录"谁还欠哪条消息"（与成功游标解耦）；
#     失败群入队，任何一路径成功送达都会清除对应欠账（_purge_delivered）
#   - in-flight 注册表保证同一 (频道, 消息, 群) 同时只有一个发送者，
#     retry / relay / backfill 统一走 send_and_record 判重后发送
#   - 队列仅存内存（存规范化消息 dict，重试时经 build_parts 重建内容，
#     不缓存媒体字节），不持久化：
#     进程重启后未跳过的缺口由 backfill 按低游标兜底
# =====================================================

RETRY_BACKOFFS = (30.0, 60.0, 120.0)  # 第 1/2/3 次重试前等待秒数
MAX_RETRY_ATTEMPTS = 3
MAX_TOTAL_PENDING = 10
MAX_PER_CHANNEL_PENDING = 5

# 测试可关闭：登记失败时自动拉起频道重试循环，改由测试手动驱动
_LOOP_AUTO_START = True

_pending = {}        # channel_id -> {message_id: _RetryEntry}
_inflight = set()    # {(channel_id, message_id, group_openid)}
_retry_loops = set()  # 已拉起的频道重试循环（防重复拉起）
_retry_tasks = set()  # 持有任务引用防 GC


class _RetryEntry:
    """一条待重试消息：内容重建所需的规范化 dict + 欠账群"""

    __slots__ = ("message", "source_label", "groups", "attempts", "next_try")

    def __init__(self, message, source_label, groups, next_try):
        self.message = message
        self.source_label = source_label
        self.groups = set(groups)
        self.attempts = 0
        self.next_try = next_try


def _claim(channel_id, message_id, group_openid):
    """在途占用（同步、无 await，天然原子）"""
    key = (channel_id, message_id, group_openid)
    if key in _inflight:
        return False
    _inflight.add(key)
    return True


def _release(channel_id, message_id, group_openid):
    _inflight.discard((channel_id, message_id, group_openid))


def _remove_entry(channel_id, message_id):
    entries = _pending.get(channel_id)
    if not entries:
        return
    entries.pop(message_id, None)
    if not entries:
        _pending.pop(channel_id, None)


def _purge_delivered(channel_id, message_id, success_groups):
    """某群已收到该消息（无论哪条路径送达），清除其欠账"""
    entry = (_pending.get(channel_id) or {}).get(message_id)
    if not entry:
        return
    for group in success_groups:
        entry.groups.discard(group)
    if not entry.groups:
        _remove_entry(channel_id, message_id)


def schedule_retry(
    channel_id,
    message_id,
    message,
    failed_groups,
    source_label="自动转发来自",
):
    """登记失败消息的延迟重试；返回是否新建队列条目。

    已存在条目时只合并失败群（不重复排队）；
    队列满 / 无失败群时返回 False，缺口交给 backfill 兜底。"""
    groups = [g for g in failed_groups if g]
    if not groups:
        return False

    entry = (_pending.get(channel_id) or {}).get(message_id)

    if entry:
        entry.groups.update(groups)
        return False

    total = sum(len(ch) for ch in _pending.values())
    per_channel = len(_pending.get(channel_id) or {})
    if total >= MAX_TOTAL_PENDING or per_channel >= MAX_PER_CHANNEL_PENDING:
        logger.warning(
            "延迟重试队列已满，丢弃 频道{} 消息{}（等待重连补发）",
            channel_id,
            message_id,
        )
        return False

    _pending.setdefault(channel_id, {})[message_id] = _RetryEntry(
        message,
        source_label,
        groups,
        time.monotonic() + RETRY_BACKOFFS[0],
    )

    if _LOOP_AUTO_START:
        _ensure_loop(channel_id)

    return True


def _ensure_loop(channel_id):
    if not _LOOP_AUTO_START:
        return
    if channel_id in _retry_loops:
        return
    _retry_loops.add(channel_id)
    _retry_tasks.add(
        asyncio.create_task(_loop_wrapper(channel_id))
    )


async def _loop_wrapper(channel_id):
    try:
        await _channel_retry_loop(channel_id)
    finally:
        _retry_loops.discard(channel_id)


async def _channel_retry_loop(channel_id):
    while _pending.get(channel_id):
        await _process_due(channel_id)
        if not _pending.get(channel_id):
            break
        await asyncio.sleep(1.0)


async def _process_due(channel_id):
    """处理该频道所有到期的待重试条目（重试循环与测试共用）"""
    entries = _pending.get(channel_id)
    if not entries:
        return

    now = time.monotonic()

    for message_id in list(entries):
        entry = entries.get(message_id)
        if (
            entry is None
            or not entry.groups
            or entry.next_try > now
        ):
            continue

        logger.info(
            "延迟重试发送(第{}次) 频道{} 消息{} 群{}",
            entry.attempts + 1,
            channel_id,
            message_id,
            sorted(entry.groups),
        )

        try:
            text_parts, media_items, has_content = await build_parts(
                entry.message,
                source_label=entry.source_label,
            )
        except Exception:
            logger.warning(
                "延迟重试内容重建失败，放弃 频道{} 消息{}",
                channel_id,
                message_id,
            )
            _remove_entry(channel_id, message_id)
            continue

        if not has_content:
            logger.warning(
                "延迟重试无可发送内容，放弃 频道{} 消息{}",
                channel_id,
                message_id,
            )
            _remove_entry(channel_id, message_id)
            continue

        ok_map = await send_and_record(
            channel_id,
            message_id,
            text_parts,
            media_items,
            list(entry.groups),
        )

        # send_and_record 成功时已 purge：全部送达则条目被移除
        entry = entries.get(message_id)
        if entry is None:
            logger.success(
                "延迟重试成功已补发 频道{} 消息{}",
                channel_id,
                message_id,
            )
            continue

        entry.attempts += 1

        if entry.attempts >= MAX_RETRY_ATTEMPTS:
            logger.error(
                "延迟重试已达上限({}次)放弃，等待重连补发 频道{} 消息{} 群{}",
                MAX_RETRY_ATTEMPTS,
                channel_id,
                message_id,
                sorted(entry.groups),
            )
            _remove_entry(channel_id, message_id)
            continue

        entry.next_try = now + RETRY_BACKOFFS[entry.attempts - 1]


async def send_and_record(
    channel_id,
    message_id,
    text_parts,
    media_items,
    groups,
):
    """统一发送入口（relay / backfill / retry 共用）：
    在途判重 → 发送 → 只进不退记录 → 清除欠账。

    返回 {group_openid: 是否送达}。同一 (频道, 消息, 群) 同时只允许
    一个发送者：占用失败（他人正在发送）的群记为未送达，由调用方
    登记延迟重试，成功路径的 purge 会清除对应欠账。"""
    claimable = [
        group
        for group in groups
        if _claim(channel_id, message_id, group)
    ]

    if not claimable:
        return {group: False for group in groups}

    try:
        ok_map = await send_relay_message(
            text_parts,
            media_items,
            claimable,
            channel_id=channel_id,
            message_id=message_id,
        )

        success_groups = [
            group
            for group, ok in ok_map.items()
            if ok
        ]

        if success_groups:
            await update_last_messages(
                lambda last: apply_success(
                    normalize_channel_map(last, channel_id),
                    {group: True for group in success_groups},
                    message_id,
                )
            )
            _purge_delivered(
                channel_id,
                message_id,
                success_groups,
            )

        return ok_map

    finally:
        for group in claimable:
            _release(channel_id, message_id, group)