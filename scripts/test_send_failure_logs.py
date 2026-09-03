"""验证 plugins/sender.py 转发失败日志的定位信息。

覆盖：
1. 429 限流失败：warning/error 均带 频道 + 消息 + 群，且标注"（限流）"
2. 非限流失败（如超时）：同上，标注"（非限流）"；不传频道/消息时用 "-" 占位
3. 发送成功路径不受影响
4. 媒体上传失败降级日志：至少带 message_id，并列出失败群
5. 多群部分失败汇总日志：只打印部分失败，全成功/全失败不打印
6. 全部失败时不打印汇总（沿用调用方原有失败日志）

直接捕获 loguru 输出，检查实际日志格式。

用法：.venv/Scripts/python.exe scripts/test_send_failure_logs.py
"""
import asyncio

import nonebot

nonebot.init()

from nonebot import logger

from plugins import sender
from plugins.media import make_text


async def _instant(_):
    """取消真实退避等待，测试立刻完成"""
    return None


def capture_logs():
    records = []
    sink_id = logger.add(
        lambda msg: records.append(msg),
        level="DEBUG",
        format="{message}",
        colorize=False,
        enqueue=False,
    )
    return records, sink_id


def stop_capture(sink_id):
    logger.remove(sink_id)


class FailingBot:
    """每次 send_to_group 都抛指定异常的假 QQ Bot"""

    def __init__(self, exc):
        self._exc = exc

    async def send_to_group(self, group_openid, message):
        raise self._exc


class OkBot:
    """send_to_group 直接成功的假 QQ Bot"""

    def __init__(self):
        self.sent = []

    async def send_to_group(self, group_openid, message):
        self.sent.append(group_openid)


class RateLimited(Exception):
    """模拟 QQ 429 限流异常（带 status_code 字段）"""

    status_code = 429

    def __init__(self):
        super().__init__("429 Too Many Requests (rate limit)")


async def test_rate_limit_log_format():
    records, sink_id = capture_logs()
    original_sleep = sender.asyncio.sleep
    sender.asyncio.sleep = _instant
    try:
        ok = await sender._send_with_retry(
            FailingBot(RateLimited()),
            "G1",
            object(),
            channel_id="CH100",
            message_id="MSG200",
        )
    finally:
        sender.asyncio.sleep = original_sleep
    stop_capture(sink_id)

    joined = "\n".join(records)
    assert ok is False, "429 重试耗尽应返回 False"
    assert "发送失败(第1次) 频道CH100 消息MSG200 群G1" in joined, joined
    assert "发送失败(第2次) 频道CH100 消息MSG200 群G1" in joined, joined
    assert "发送失败(第3次) 频道CH100 消息MSG200 群G1" in joined, joined
    assert "发送失败已达上限(3次)（限流） 频道CH100 消息MSG200 群G1" in joined, joined
    print("[OK] 429 限流失败日志 →\n" + "\n".join(records[:2]) + "\n...")


async def test_non_rate_limit_log_format_with_defaults():
    records, sink_id = capture_logs()
    original_sleep = sender.asyncio.sleep
    sender.asyncio.sleep = _instant
    try:
        ok = await sender._send_with_retry(
            FailingBot(RuntimeError("timed out (read timeout)")),
            "G1",
            object(),
        )
    finally:
        sender.asyncio.sleep = original_sleep
    stop_capture(sink_id)

    joined = "\n".join(records)
    assert ok is False
    # 不传频道/消息时用 "-" 占位（command.py 手动转发路径）
    assert "发送失败(第1次) 频道- 消息- 群G1: timed out" in joined, joined
    assert "发送失败已达上限(3次)（非限流） 频道- 消息- 群G1" in joined, joined
    print("[OK] 非限流失败日志（默认占位） →\n" + records[-1])


async def test_success_path_unchanged():
    records, sink_id = capture_logs()
    bot = OkBot()
    try:
        ok = await sender._send_with_retry(
            bot,
            "G1",
            object(),
            channel_id="CH100",
            message_id="MSG200",
        )
    finally:
        pass
    stop_capture(sink_id)

    assert ok is True
    assert bot.sent == ["G1"]
    assert records == [], "成功路径不应产生任何失败日志"
    print("[OK] 发送成功路径无失败日志，返回 True")


async def test_media_degradation_log_has_message_id():
    records, sink_id = capture_logs()
    original_send_to_groups = sender._send_to_groups
    calls = []

    async def fake_send_to_groups(qq_bot, groups, message, channel_id="", message_id=""):
        calls.append(message)
        if len(calls) == 1:
            # 第一次（富媒体）全部失败 → 触发降级
            return {g: False for g in groups}
        # 第二次（降级链接）全部成功 → 降级视为已送达
        return {g: True for g in groups}

    sender._send_to_groups = fake_send_to_groups
    try:
        ok_map = await sender.send_media_items(
            None,
            ["G1", "G2"],
            [{"kind": "图片", "url": "https://x/y.png", "segment": make_text("媒体占位")}],
            channel_id="CH100",
            message_id="MSG200",
        )
    finally:
        sender._send_to_groups = original_send_to_groups
    stop_capture(sink_id)

    joined = "\n".join(records)
    assert ok_map == {"G1": True, "G2": True}, "降级为链接应视为已送达"
    assert "富媒体上传失败，降级为链接: 图片 频道CH100 消息MSG200 失败群['G1', 'G2']" in joined, joined
    print("[OK] 媒体降级日志 →\n" + records[-1])


async def test_partial_failure_summary():
    records, sink_id = capture_logs()
    sender.log_partial_failure(
        {"G1": True, "G2": False, "G3": False},
        "CH100",
        "MSG200",
    )
    # 全成功：不打印
    sender.log_partial_failure(
        {"G1": True, "G2": True},
        "CH100",
        "MSG200",
    )
    # 全失败：不打印（由调用方原有失败日志负责）
    sender.log_partial_failure(
        {"G1": False, "G2": False},
        "CH100",
        "MSG200",
    )
    stop_capture(sink_id)

    joined = "\n".join(records)
    assert joined.count("部分群发送失败") == 1, joined
    assert "部分群发送失败: 频道CH100 消息MSG200 失败群: ['G2', 'G3']" in joined, joined
    print("[OK] 部分失败汇总日志 →\n" + records[0])


async def main():
    await test_rate_limit_log_format()
    await test_non_rate_limit_log_format_with_defaults()
    await test_success_path_unchanged()
    await test_media_degradation_log_has_message_id()
    await test_partial_failure_summary()
    print("\n全部通过")


if __name__ == "__main__":
    asyncio.run(main())