"""验证 plugins/fetch.py 的 Discord 回复引用 → QQ 引用行逻辑。

覆盖：
1. REST 内联 referenced_message → 引用行一步到位
2. 事件路径仅有 message_reference.message_id → 回退抓取原文 → 引用行
3. 回退抓取失败 → 静默跳过引用行，当前消息正常转发
4. 非回复消息 → 输出与改动前一致，无引用行
5. 长正文截断到 REFERENCE_TEXT_LIMIT

直接打印 build_parts 产出的 text_parts，检查实际转发格式。

用法：.venv/Scripts/python.exe scripts/test_fetch_reference.py
"""
import asyncio
import unittest.mock as mock

import nonebot

nonebot.init()

from plugins import fetch


def text_parts_str(text_parts):
    """把 QQ 文本消息段拼成纯文本"""
    return "".join(
        (getattr(p, "data", None) or {}).get("text", "")
        for p in text_parts
    )


def make_event(channel_id, content, ref=None, author_name="eve"):
    """构造兼容 normalize_event 的事件替身（getattr 取值即可）"""
    author = type("A", (), {"username": author_name, "global_name": author_name.capitalize()})()
    return type(
        "Ev",
        (),
        {
            "channel_id": channel_id,
            "content": content,
            "embeds": [],
            "attachments": [],
            "message_reference": ref,
            "author": author,
        },
    )()


async def test_inline_reference():
    # REST：内联 referenced_message 一步到位，无需额外请求
    raw = {
        "id": "1001",
        "channel_id": "111",
        "author": {"username": "alice", "global_name": "Alice"},
        "content": "当前消息正文",
        "embeds": [],
        "attachments": [],
        "message_reference": {"message_id": "999", "channel_id": "111", "guild_id": "1"},
        "referenced_message": {
            "id": "999",
            "channel_id": "111",
            "author": {"username": "bob", "global_name": "Bob"},
            "content": "被回复内容",
            "embeds": [],
            "attachments": [],
        },
    }
    msg = fetch.normalize_api_message(raw)
    assert msg["reference"]["username"] == "Bob", msg["reference"]
    assert msg["reference"]["content"] == "被回复内容"
    msg["channel_name"] = "频道A"
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎ Bob：被回复内容" in text, text
    # 引用行在 header 之后、当前消息正文之前
    assert text.index("↩︎ Bob：被回复内容") < text.index("当前消息正文"), text
    assert has_content
    print("[OK] 内联 referenced_message →\n" + text)


async def test_event_reference_fallback_success():
    # 事件：只有 message_reference.message_id，回退抓取原文成功
    ref = type("R", (), {"message_id": 999, "channel_id": 111})()
    event = make_event("111", "事件正文", ref=ref, author_name="carol")

    msg = fetch.normalize_event(event, "频道A")
    assert msg["reference"]["message_id"] == "999", msg["reference"]
    assert msg["reference"]["content"] == "", msg["reference"]

    async def fake_fetch(channel_id, message_id):
        assert channel_id == "111" and message_id == "999"
        return {
            "id": "999",
            "channel_id": "111",
            "author": {"username": "dave", "global_name": "Dave"},
            "content": "回退抓取到的原文",
            "embeds": [],
            "attachments": [],
        }

    with mock.patch.object(fetch, "_fetch_reference_message", new=fake_fetch):
        parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎ Dave：回退抓取到的原文" in text, text
    assert text.index("↩︎ Dave") < text.index("事件正文"), text
    assert has_content
    print("[OK] 事件回退抓取成功 →\n" + text)


async def test_reference_fetch_failure():
    # 回退抓取失败 → 静默跳过引用行，当前消息仍正常转发
    ref = type("R", (), {"message_id": 999, "channel_id": 111})()
    event = make_event("111", "当前消息仍转发", ref=ref, author_name="eve")

    msg = fetch.normalize_event(event, "频道A")
    assert msg["reference"] is not None

    async def fake_fetch(channel_id, message_id):
        return None  # 抓取失败

    with mock.patch.object(fetch, "_fetch_reference_message", new=fake_fetch):
        parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎" not in text, text
    assert "当前消息仍转发" in text, text
    assert has_content
    print("[OK] 回退抓取失败 → 无引用行，当前消息正常 →\n" + text)


async def test_non_reply_unchanged():
    # 非回复消息：reference 为 None，输出与改动前完全一致
    raw = {
        "id": "1001",
        "channel_id": "111",
        "author": {"username": "alice"},
        "content": "普通消息",
        "embeds": [],
        "attachments": [],
    }
    assert fetch.normalize_api_message(raw)["reference"] is None

    msg = {
        "username": "alice",
        "channel_id": "111",
        "channel_name": "频道A",
        "content": "普通消息",
        "embeds": [],
        "attachments": [],
    }
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    expected = (
        "自动转发来自\n"
        "Discord [#频道A] 中 [alice]的消息：\n"
        "普通消息"
    )
    assert text == expected, repr(text)
    assert "↩︎" not in text
    assert has_content
    print("[OK] 非回复消息输出不变 →\n" + text)


async def test_long_content_truncated():
    # 长正文截断到 REFERENCE_TEXT_LIMIT + "…"
    raw = {
        "id": "1001",
        "channel_id": "111",
        "author": {"username": "alice"},
        "content": "当前消息",
        "embeds": [],
        "attachments": [],
        "message_reference": {"message_id": "999", "channel_id": "111"},
        "referenced_message": {
            "id": "999",
            "channel_id": "111",
            "author": {"username": "bob"},
            "content": "很长的被回复内容" * 20,
            "embeds": [],
            "attachments": [],
        },
    }
    msg = fetch.normalize_api_message(raw)
    msg["channel_name"] = "频道A"
    parts, _media, _has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    line = next(l for l in text.splitlines() if l.startswith("↩︎ "))
    prefix = "↩︎ bob："
    assert len(line) == len(prefix) + fetch.REFERENCE_TEXT_LIMIT + 1, repr(line)
    assert line.endswith("…"), repr(line)
    print(f"[OK] 长正文截断 → {line}")


async def main():
    await test_inline_reference()
    print()
    await test_event_reference_fallback_success()
    print()
    await test_reference_fetch_failure()
    print()
    await test_non_reply_unchanged()
    print()
    await test_long_content_truncated()
    print("\n全部通过")


if __name__ == "__main__":
    asyncio.run(main())
