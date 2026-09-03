"""验证 plugins/retry.py 延迟重试 + 在途判重 + 只进不退游标。

覆盖（对应需求）：
1. M1 失败 → M2 成功 → M1 延迟重试成功，M1 不丢
2. retry 与 backfill 并发处理同一条消息不会重复发送
3. 游标只进不退（不回退到已过去的失败消息）
4. 多群部分失败只重试失败群
5. 重试全部失败后，现有 backfill 仍能按游标兜底
6. 正常成功消息无额外重试

用法：.venv/Scripts/python.exe scripts/test_retry_recovery.py
"""
import asyncio
import copy
import unittest.mock as mock

import nonebot

nonebot.init()

from plugins import dedup
from plugins import retry as retry_mod


class FakeStore:
    """替身：load 返回最新副本；update 应用变更（模拟锁内读改写）"""

    def __init__(self, initial=None):
        self.state = copy.deepcopy(initial) if initial is not None else {}

    def load(self):
        return copy.deepcopy(self.state)

    async def update(self, mutate):
        last = copy.deepcopy(self.state)
        changed = mutate(last)
        if changed:
            self.state = last
        return changed


class FakeSender:
    """替身：按 (channel, message) 脚本化保底结果，可模拟延迟"""

    def __init__(self, results=None, delay=0.0):
        self.results = results or {}
        self.delay = delay
        self.calls = []  # (channel_id, message_id, groups)

    async def __call__(
        self,
        text_parts,
        media_items,
        groups=None,
        channel_id="",
        message_id="",
    ):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append((channel_id, message_id, list(groups or [])))
        seq = self.results.get((channel_id, message_id))
        if seq:
            scripted = seq.pop(0)
            return {g: scripted.get(g, True) for g in (groups or [])}
        return {g: True for g in (groups or [])}


async def fake_build_parts(message, source_label="自动转发来自"):
    return (["占位"], [], True)


class Ctx:
    """打补丁上下文：FakeStore + FakeSender + build_parts，
    关闭自动拉起重试循环，由测试手动驱动 _process_due"""

    def __init__(self, store_initial, sender_results=None, sender_delay=0.0):
        self.store = FakeStore(store_initial)
        self.sender = FakeSender(sender_results, sender_delay)
        self._patch = mock.patch.multiple(
            retry_mod,
            load_last_messages=self.store.load,
            update_last_messages=self.store.update,
            send_relay_message=self.sender,
            build_parts=fake_build_parts,
        )

    def __enter__(self):
        retry_mod._LOOP_AUTO_START = False
        retry_mod._pending.clear()
        retry_mod._inflight.clear()
        self._patch.start()
        retry_mod._LOOP_AUTO_START = False
        return self

    def __exit__(self, *exc):
        retry_mod._pending.clear()
        retry_mod._inflight.clear()
        retry_mod._LOOP_AUTO_START = True
        self._patch.stop()


async def test_m1_fail_m2_ok_m1_retry_not_lost():
    # M1(101) 对 B 失败 → 入队；M2(102) 成功推进 B 到 102；
    # 延迟重试补上 M1（B 收到），游标保持 102 不回退，队列清空
    initial = {"C1": {"A": "100", "B": "100"}}
    # M1：实时发送 A 成功/B 失败；重试再投时成功
    results = {("C1", "101"): [{"B": False}, {}]}
    with Ctx(initial, results) as ctx:
        ok1 = await retry_mod.send_and_record(
            "C1", "101", ["p"], [], ["A", "B"]
        )
        assert ok1 == {"A": True, "B": False}, ok1

        failed = [g for g, ok in ok1.items() if not ok]
        created = retry_mod.schedule_retry(
            "C1", "101", {"channel_id": "C1", "content": "M1"},
            failed, "自动转发来自",
        )
        assert created is True
        assert retry_mod._pending["C1"]["101"].groups == {"B"}

        ok2 = await retry_mod.send_and_record("C1", "102", ["p"], [], ["B"])
        assert ok2 == {"B": True}, ok2
        assert ctx.store.state["C1"]["B"] == "102"

        # 驱动重试
        retry_mod._pending["C1"]["101"].next_try = 0
        await retry_mod._process_due("C1")

        assert "101" not in retry_mod._pending.get("C1", {}), retry_mod._pending
        # B 收到过 M1（延迟补上），且游标不回退到 101
        assert ("C1", "101", ["B"]) in ctx.sender.calls, ctx.sender.calls
        assert ctx.store.state["C1"]["B"] == "102", ctx.store.state
        print("[OK] M1 失败→M2 成功→M1 延迟重试补上，游标保持 102")


async def test_retry_backfill_concurrent_no_duplicate():
    # retry 与 backfill 同一时刻处理 (C1,101,B)：只发一次
    initial = {"C1": {"B": "100"}}
    with Ctx(initial, sender_delay=0.05) as ctx:
        async def retry_side():
            return await retry_mod.send_and_record(
                "C1", "101", ["p"], [], ["B"]
            )

        async def backfill_side():
            return await retry_mod.send_and_record(
                "C1", "101", ["p"], [], ["B"]
            )

        results = await asyncio.gather(retry_side(), backfill_side())

        sent = [
            c for c in ctx.sender.calls
            if c[0] == "C1" and c[1] == "101"
        ]
        assert len(sent) == 1, ctx.sender.calls
        ok_values = sorted({m["B"] for m in results})
        assert ok_values == [False, True], results
        assert ctx.store.state["C1"]["B"] == "101", ctx.store.state
        print("[OK] 并发 retry/backfill 同一消息只发送一次")


async def test_cursor_never_regresses():
    cm = {"B": "102"}
    changed = dedup.apply_success(cm, {"B": True}, "101")
    assert cm["B"] == "102" and changed is False
    changed2 = dedup.apply_success(cm, {"B": True}, "103")
    assert cm["B"] == "103" and changed2 is True
    print("[OK] 游标只进不退（101 不回退，103 正常推进）")


async def test_only_failed_groups_retried():
    initial = {"C1": {"A": "100", "B": "100"}}
    results = {("C1", "101"): [{"B": False}, {}]}
    with Ctx(initial, results) as ctx:
        ok1 = await retry_mod.send_and_record(
            "C1", "101", ["p"], [], ["A", "B"]
        )
        assert ok1 == {"A": True, "B": False}, ok1

        failed = [g for g, ok in ok1.items() if not ok]
        retry_mod.schedule_retry(
            "C1", "101", {"channel_id": "C1", "content": "M1"},
            failed, "自动转发来自",
        )
        assert retry_mod._pending["C1"]["101"].groups == {"B"}

        retry_mod._pending["C1"]["101"].next_try = 0
        await retry_mod._process_due("C1")

        # 重试只发给失败的 B（单群调用，且不含 A）
        retry_calls = [
            c for c in ctx.sender.calls
            if c[0] == "C1" and c[1] == "101" and len(c[2]) == 1
        ]
        assert retry_calls and retry_calls[-1][2] == ["B"], ctx.sender.calls
        assert ctx.store.state["C1"]["A"] == "101"
        assert ctx.store.state["C1"]["B"] == "101"
        print("[OK] 多群部分失败只重试失败群 B（不影响 A）")


async def test_exhaustion_then_backfill_fallback():
    # 实时 + 3 次重试全部失败 → 队列条目移除、失败群游标不动；
    # backfill 仍能按低游标选中 B 并送达推进
    initial = {"C1": {"B": "100"}}
    results = {("C1", "101"): [{"B": False}] * 4}
    with Ctx(initial, results) as ctx:
        ok1 = await retry_mod.send_and_record("C1", "101", ["p"], [], ["B"])
        assert ok1 == {"B": False}, ok1

        retry_mod.schedule_retry(
            "C1", "101", {"channel_id": "C1", "content": "M1"},
            ["B"], "自动转发来自",
        )

        for _ in range(3):
            entry = retry_mod._pending.get("C1", {}).get("101")
            if not entry:
                break
            entry.next_try = 0
            await retry_mod._process_due("C1")

        assert "101" not in retry_mod._pending.get("C1", {}), retry_mod._pending
        assert ctx.store.state["C1"]["B"] == "100", ctx.store.state

        # backfill 兜底：低游标选中 B，送达后推进
        targets = dedup.select_target_groups(
            dedup.normalize_channel_map(copy.deepcopy(ctx.store.state), "C1"),
            ["B"],
            "101",
        )
        assert targets == ["B"], targets
        ok2 = await retry_mod.send_and_record("C1", "101", ["p"], [], targets)
        assert ok2 == {"B": True}, ok2
        assert ctx.store.state["C1"]["B"] == "101", ctx.store.state
        print("[OK] 重试耗尽后 backfill 兜底成功，游标推进到 101")


async def test_normal_success_no_retry():
    initial = {"C1": {"B": "100"}}
    with Ctx(initial) as ctx:
        ok_map = await retry_mod.send_and_record(
            "C1", "101", ["p"], [], ["B"]
        )
        assert ok_map == {"B": True}, ok_map
        assert retry_mod._pending == {}, retry_mod._pending
        assert len(ctx.sender.calls) == 1
        assert ctx.store.state["C1"]["B"] == "101"
        print("[OK] 正常成功无额外重试，仅发送一次")


async def main():
    await test_m1_fail_m2_ok_m1_retry_not_lost()
    await test_retry_backfill_concurrent_no_duplicate()
    await test_cursor_never_regresses()
    await test_only_failed_groups_retried()
    await test_exhaustion_then_backfill_fallback()
    await test_normal_success_no_retry()
    print("\n全部通过")


if __name__ == "__main__":
    asyncio.run(main())