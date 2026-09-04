# -*- coding: utf-8 -*-
"""QQ 私聊 relay 两步式交互验证脚本

私聊 relay 与群聊统一为「先指定目标，再发送下一条内容」：

  relay-list            列出可转发目标群（全部已注册且 enabled，私聊不做测试隔离）
  relay {序号}          选择第 N 个群，静默等待下一条普通消息转发
  relay all             选择全部可用群，静默等待下一条普通消息转发
  relay / relay abc / relay 999 → 无法找到对应的转发群
  下一条普通消息才执行转发；成功/失败回复结果；超时只清 pending 不回复。
  私聊专属：下一条消息若是 Discord 链接，走 fetch_message + build_parts。

覆盖点：
  1. relay-list 列出全部 enabled 群（不受测试隔离、排除 disabled）
  2. relay {序号} 静默建立 pending，下一条文本转发到目标群 → 转发成功
  3. relay all 转发到全部 enabled 群
  4. 下一条为 DC 链接 → 走 fetch_message + build_parts
  5. 无效序号（relay / relay abc / relay 999）→ 无法找到对应的转发群
  6. 超时：只清 pending，不回复、不转发
  7. pending 按 user_openid 隔离（多用户并发互不串台）
  8. 白名单校验保留

不读写任何 data/ 真实文件；不触发真实网络（全部 mock）。

运行：.venv/Scripts/python.exe 测试内容/test_qq_fwd_c2c.py
"""
import asyncio
import os
import sys
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# ---------- 绕过 nonebot 运行时：on_message / on_notice 返回假 matcher ----------
import nonebot


class _FakeMatcher:
    @staticmethod
    def handle(*a, **k):
        def deco(fn):
            return fn
        return deco


def _fake(*a, **k):
    return _FakeMatcher()


nonebot.on_message = _fake
nonebot.on_notice = _fake

import plugins.command as cmd
from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq.event import C2CMessageCreateEvent
from nonebot.adapters.qq.models.qq import FriendAuthor


results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(detail)) if detail else ""))


class StubBot(QQBot):
    def __init__(self):
        self.sent = []

    async def send(self, event, message, **kwargs):
        self.sent.append(str(message))
        return None


def make_c2c(openid, text, msg_id="M"):
    return C2CMessageCreateEvent(
        id=msg_id,
        timestamp="2026-09-03T12:00:00+08:00",
        content=text,
        author=FriendAuthor(id=openid, user_openid=openid, username="测试用户"),
        to_me=True,
    )


class FixedClock:
    def __init__(self, base=1000000.0):
        self.t = [float(base)]

    def time(self):
        return self.t[0]

    def advance(self, delta):
        self.t[0] += delta


CLOCK = FixedClock(1000000.0)
NOW = 1000000.0

G_A = "GRP_C2C_A"
G_B = "GRP_C2C_B"
G_DISABLED = "GRP_C2C_DISABLED"
U1 = "USER_C2C_1"
U2 = "USER_C2C_2"
U_NOAUTH = "USER_C2C_NOAUTH"

BOT = StubBot()


def entries():
    """全部已注册群：两个 enabled + 一个 disabled（私聊不区分测试/正式）"""
    return [
        {"openid": G_A, "enabled": True, "name": "测试群A"},
        {"openid": G_B, "enabled": True, "name": "正式群B"},
        {"openid": G_DISABLED, "enabled": False, "name": "停用群"},
    ]


def env():
    from contextlib import ExitStack

    class _Nested:
        def __init__(self):
            self._stack = ExitStack()

        def __enter__(self):
            self._stack.enter_context(patch("plugins.command.time", CLOCK))
            self._stack.enter_context(patch("plugins.command.get_qq_group_entries", side_effect=entries))
            self._stack.enter_context(patch("plugins.command.get_qq_fwd_select_timeout", return_value=60))
            self._stack.enter_context(patch("plugins.command.get_active_user_openids",
                                            return_value=[U1, U2]))
            self._stack.enter_context(patch("plugins.command.get_active_groups",
                                            return_value=[G_A, G_B]))
            return self._stack.__enter__()

        def __exit__(self, *exc):
            return self._stack.__exit__(*exc)

    return _Nested()


def reset_state():
    cmd._c2c_pending.clear()
    BOT.sent.clear()


def text_of(parts):
    return "".join(
        (getattr(p, "data", None) or {}).get("text", "") for p in parts
    )


def run(coro):
    return asyncio.run(coro)


# =====================================================
# A. relay-list
# =====================================================
print("== A. relay-list ==")
reset_state()
with env():
    BOT.sent.clear()
    run(cmd.handle(BOT, make_c2c(U1, "relay-list")))
    replied = "\n".join(BOT.sent)
    check("A1 列出全部 enabled 群（测试+正式，无隔离）",
          "Relay群列表" in replied and "1. 测试群A" in replied
          and "2. 正式群B" in replied, replied)
    check("A2 不含 disabled 群", "停用群" not in replied, replied)

# =====================================================
# B. relay {序号} → 下一条文本转发
# =====================================================
print("== B. relay {序号} → 下一条文本 ==")
reset_state()
CLOCK.t = [NOW]
with env():
    # relay 1 → 静默建立 pending（选择测试群A）
    run(cmd.handle(BOT, make_c2c(U1, "relay 1")))
    check("B1 relay 1 静默建立 pending", cmd.get_c2c_pending(U1) is not None
          and BOT.sent == [], BOT.sent)

    sent = {}

    async def fake_send(text_parts, media_items, groups=None):
        sent["groups"] = list(groups)
        sent["text"] = text_of(text_parts)
        return {g: True for g in groups}

    with patch("plugins.command.send_relay_message", side_effect=fake_send):
        BOT.sent.clear()
        run(cmd.handle(BOT, make_c2c(U1, "这是一条要转发的内容")))
    check("B2 下一条文本转发到目标群", sent.get("groups") == [G_A], sent)
    check("B2 内容保留", sent.get("text") == "这是一条要转发的内容", sent)
    check("B2 回复转发成功", any("转发成功" in s for s in BOT.sent), BOT.sent)
    check("B2 pending 已清除", cmd.get_c2c_pending(U1) is None)

# =====================================================
# C. relay all → 转发到全部 enabled 群
# =====================================================
print("== C. relay all ==")
reset_state()
CLOCK.t = [NOW]
with env():
    run(cmd.handle(BOT, make_c2c(U1, "relay all")))
    check("C1 relay all 建立 pending(all)", cmd.get_c2c_pending(U1) is not None
          and cmd.get_c2c_pending(U1)["targets"] == "all", cmd._c2c_pending)

    sent = {}

    async def fake_send(text_parts, media_items, groups=None):
        sent["groups"] = list(groups)
        sent["text"] = text_of(text_parts)
        return {g: True for g in groups}

    with patch("plugins.command.send_relay_message", side_effect=fake_send):
        BOT.sent.clear()
        run(cmd.handle(BOT, make_c2c(U1, "全员消息")))
    check("C2 转发到全部 enabled 群", sent.get("groups") == [G_A, G_B], sent)
    check("C2 回复转发成功", any("转发成功" in s for s in BOT.sent), BOT.sent)

# =====================================================
# D. 下一条为 DC 链接 → 走 fetch_message + build_parts
# =====================================================
print("== D. DC 链接（私聊专属）==")
reset_state()
CLOCK.t = [NOW]
with env():
    run(cmd.handle(BOT, make_c2c(U1, "relay 1")))

    fetched = {}

    async def fake_fetch(url):
        fetched["url"] = url
        return {"channel_id": "C123", "content": "discord 内容"}, None

    async def fake_build(msg, source_label=None):
        return [type("P", (), {"data": {"text": "dc:" + msg["content"]}})], [], True

    sent = {}

    async def fake_send(text_parts, media_items, groups=None):
        sent["groups"] = list(groups)
        return {g: True for g in groups}

    with patch("plugins.command.fetch_message", side_effect=fake_fetch), \
         patch("plugins.command.build_parts", side_effect=fake_build), \
         patch("plugins.command.get_all_channels", return_value={}), \
         patch("plugins.command.send_relay_message", side_effect=fake_send):
        BOT.sent.clear()
        run(cmd.handle(BOT, make_c2c(U1, "https://discord.com/channels/1/2/3")))
    check("D1 DC 链接走 fetch_message",
          fetched.get("url") == "https://discord.com/channels/1/2/3", fetched)
    check("D2 转发到目标群且回复成功",
          sent.get("groups") == [G_A] and any("转发成功" in s for s in BOT.sent),
          (sent, BOT.sent))

# =====================================================
# E. 无效序号
# =====================================================
print("== E. 无效序号 ==")
reset_state()
CLOCK.t = [NOW]
with env():
    for bad, label in [("relay", "裸relay"), ("relay abc", "非数字"),
                       ("relay 999", "越界")]:
        BOT.sent.clear()
        run(cmd.handle(BOT, make_c2c(U1, bad)))
        check(f"E {label} 提示无法找到",
              any("无法找到对应的转发群" in s for s in BOT.sent)
              and cmd.get_c2c_pending(U1) is None, BOT.sent)

# =====================================================
# F. 超时：只清 pending，不回复、不转发
# =====================================================
print("== F. 超时 ==")
reset_state()
CLOCK.t = [NOW]
with env():
    run(cmd.handle(BOT, make_c2c(U1, "relay 1")))
    CLOCK.advance(61)
    BOT.sent.clear()

    async def never_send(*a, **k):
        raise AssertionError("超时后不应转发")

    with patch("plugins.command.send_relay_message", side_effect=never_send):
        run(cmd.handle(BOT, make_c2c(U1, "迟到的内容")))
    check("F1 超时后不转发不回复",
          BOT.sent == [] and cmd.get_c2c_pending(U1) is None, BOT.sent)

# =====================================================
# G. 多用户并发：pending 按 user 隔离
# =====================================================
print("== G. 多用户并发 ==")
reset_state()
CLOCK.t = [NOW]
with env():
    run(cmd.handle(BOT, make_c2c(U1, "relay 1")))
    run(cmd.handle(BOT, make_c2c(U2, "relay 2")))

    sent1, sent2 = [], []

    async def fake_send1(text_parts, media_items, groups=None):
        sent1.append((text_of(text_parts), list(groups)))
        return {g: True for g in groups}

    async def fake_send2(text_parts, media_items, groups=None):
        sent2.append((text_of(text_parts), list(groups)))
        return {g: True for g in groups}

    with patch("plugins.command.send_relay_message", side_effect=fake_send1):
        run(cmd.handle(BOT, make_c2c(U1, "U1的内容")))
    with patch("plugins.command.send_relay_message", side_effect=fake_send2):
        run(cmd.handle(BOT, make_c2c(U2, "U2的内容")))

    check("G1 U1 转发到自己的目标(测试群A)", sent1 == [("U1的内容", [G_A])], sent1)
    check("G2 U2 转发到自己的目标(正式群B)", sent2 == [("U2的内容", [G_B])], sent2)
    check("G3 两人 pending 各自清除",
          cmd.get_c2c_pending(U1) is None and cmd.get_c2c_pending(U2) is None)

# =====================================================
# H. 白名单校验
# =====================================================
print("== H. 白名单 ==")
reset_state()
with env():
    BOT.sent.clear()
    run(cmd.handle(BOT, make_c2c(U_NOAUTH, "relay-list")))
    check("H1 未授权用户被拒绝", any("未授权" in s for s in BOT.sent), BOT.sent)
    check("H2 未授权不建立 pending", cmd.get_c2c_pending(U_NOAUTH) is None)

print()
failed = [x for x in results if not x[1]]
print("总计 %d 项，通过 %d 项，失败 %d 项" % (len(results), len(results) - len(failed), len(failed)))
if failed:
    for name, _, detail in failed:
        print("FAIL: " + name + " | " + str(detail))
    sys.exit(1)
print("全部通过")