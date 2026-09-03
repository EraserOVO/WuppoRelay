# -*- coding: utf-8 -*-
"""QQ→QQ 手动转发。

任何 QQ 用户在一个已注册的 QQ 接收群里发送 relay，Bot 立即锁定该用户
在该群最近一条普通消息，列出一份「可转发目标群」供用户用序号选择，然后
把锁定的那条消息转发到所选目标群。

关键约束：
  - 不依赖白名单：任何 QQ 用户可触发（与 register 的开放策略一致）。
  - 不依赖转发组（forwarding_groups 只表达 Discord频道→QQ群 路由）。
  - 测试群只能选其他测试群，非测试群只能选其他非测试群，排除当前群。
  - 交互状态绑定 (当前群, 发送者 member_openid)，防多人串台。
  - 过滤 Bot 自己的消息（author.bot == True），防止把 Bot 消息当用户上一条。
  - “上一条消息”在 relay 指令到来时即锁定，且只接受 recency_limit 秒内的。
  - 目标群在实际发送前再次校验 enabled / is_test，防交互期间配置变化。

发送复用 plugins/sender（分片/重试/限流退避/媒体降级）与
plugins/media（QQ 图片/音视频的字节上传+降级链接）。本模块把“发送”收口到
模块级 custom_send（默认 sender.send_relay_message），便于测试注入桩实现。
"""
import asyncio
import time

import httpx

from nonebot import logger
from nonebot import on_message
from nonebot.adapters import Bot, Event

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import MessageSegment as QQMessageSegment
from nonebot.adapters.qq.event import GroupMessageCreateEvent

from plugins.config import (
    get_qq_group_entries,
    get_qq_fwd_recency_limit,
    get_qq_fwd_select_timeout,
)
from plugins.media import make_text, make_media_link
from plugins.sender import send_relay_message


# =====================================================
# 指令词
# =====================================================
FWD_CMD = "relay"


# =====================================================
# 群消息缓存：按 (群, 用户) 记录最近一条普通消息
#
# 只在收到群消息事件时实时记录（QQ 官方机器人没有按群按用户反查历史
# 的接口，历史消息内容只存在于实时事件里）。记录时会过滤：
#   - Blot 自己的消息（author.bot == True）
#   - 纯指令消息（relay 或序号选择），避免把交互提示/选择回复当用户
#     普通消息缓存
# 每条带 time.time() 捕获时间戳，供 recency_limit 判定。
# 内存结构（不落盘，Instant 场景无持久化必要）：
#   _recent[(group, user)] = {...归一化消息}
# =====================================================

# _recent 缓存上限：防止群消息量大时内存无限增长（超出按近似 LRU 丢弃）
_RECENT_CACHE_MAX = 2048
# 已经转发过一次的消息 id 集合：防止同一消息（尤其 Bot 转发的）被反复命中
_forwarded_msg_ids = set()
_FORWARDED_IDS_MAX = 4096

# 按 (群openid, 用户member_openid) 记录的最近一条普通消息
_recent = {}


def _normalize_group_message(group_openid, member_openid, content, attachments, ts, msg_id=None):
    """把原始群消息归一化为缓存条目（供测试直接构造，与事件解耦）。"""
    return {
        "group": group_openid,
        "user": member_openid,
        "content": content,
        "attachments": list(attachments or []),  # [{url, content_type, ...}]
        "ts": ts,
        "msg_id": msg_id,
    }


def _is_fwd_command(text):
    return text is not None and text == FWD_CMD


def capture_recent_message(entry):
    """写入按 (群, 用户) 的最近消息缓存；返回 True 表示已写入。

    entry 为 _normalize_group_message 的产物。过滤逻辑：
      - 纯指令消息（relay）不入缓存
      - 空内容且无附件的消息不入缓存
    Bot 过滤与“选择序号回复”过滤在事件层（mark_recent_from_event）完成，
    因为后者需要感知进行中的交互状态。
    """
    text = (entry.get("content") or "").strip()
    if _is_fwd_command(text):
        return False
    if not text and not entry.get("attachments"):
        return False

    _recent[(entry["group"], entry["user"])] = entry
    if len(_recent) > _RECENT_CACHE_MAX:
        # 近似丢弃最早写入的桶（dict 保持插入序）
        oldest_key = next(iter(_recent))
        _recent.pop(oldest_key, None)
    return True


def get_recent_message(group_openid, member_openid):
    """返回该用户在该群最近一条普通消息；无则返回 None"""
    return _recent.get((group_openid, member_openid))


def mark_forwarded(msg_id):
    """标记某条消息已转发，防止同一条消息被反复转发（死循环兜底）。"""
    if not msg_id:
        return
    _forwarded_msg_ids.add(msg_id)
    if len(_forwarded_msg_ids) > _FORWARDED_IDS_MAX:
        _forwarded_msg_ids.clear()


def already_forwarded(msg_id):
    return bool(msg_id) and msg_id in _forwarded_msg_ids


def mark_recent_from_event(bot, event):
    """事件层钩子：把一条真实群消息写入最近消息缓存（过滤 Bot/指令）。

    返回是否写入；供 on_message 处理器调用。"""
    if not isinstance(bot, QQBot):
        return False
    if not isinstance(event, GroupMessageCreateEvent):
        return False
    if getattr(event.author, "bot", False):
        return False  # 过滤 Bot 自己发的消息

    content = event.get_plaintext() or ""
    # 进行中的交互选择回复（如“2”）不入缓存：否则用户选择后其“最近
    # 消息”会变成选择序号。序号也需同时是一条真实消息才会触发转发缓存。
    if is_pending_interaction(event.group_openid, event.author.member_openid, content):
        return False
    attachments = [
        {"url": a.url, "content_type": a.content_type or ""}
        for a in (event.attachments or [])
        if getattr(a, "url", None)
    ]
    entry = _normalize_group_message(
        event.group_openid,
        event.author.member_openid,
        content,
        attachments,
        time.time(),
        msg_id=getattr(event, "id", None),
    )
    return capture_recent_message(entry)


# =====================================================
# 目标群计算（纯函数）
#
# 候选 = 已注册(enabled) 且 is_test 与当前群一致 且 非当前群。
# 独立于 forwarding_groups。
# =====================================================

def compute_target_list(entries, current_openid, current_is_test):
    """从群条目列表计算可转发目标群。

    返回 [{"openid", "name", "is_test"}]；只含 enabled 且 is_test 匹配且非当前群。
    顺序与 settings 中条目顺序一致。
    """
    current = str(current_openid)
    targets = []
    seen = set()
    for item in entries:
        oid = str(item.get("openid") or "")
        if not oid or oid in seen:
            continue
        seen.add(oid)
        if oid == current:
            continue
        if not item.get("enabled"):
            continue
        if bool(item.get("is_test")) != bool(current_is_test):
            continue
        targets.append({
            "openid": oid,
            "name": str(item.get("name") or item.get("remark") or "").strip(),
            "is_test": bool(item.get("is_test")),
        })
    return targets


def build_target_menu(targets, select_timeout):
    """把目标群列表渲染成供用户选择的消息文本。"""
    lines = ["【可转发目标群】", "请回复序号选择要转发的群"]
    if not targets:
        lines.insert(1, "当前没有可转发的目标群（没有已启用且类型匹配的群）")
    for i, t in enumerate(targets, start=1):
        label = t["name"] or t["openid"]
        mark = "（测试）" if t["is_test"] else ""
        lines.append(f"{i}. {label}{mark}")
    lines.append(
        f"（{select_timeout}秒内回复序号有效，回复其他内容可取消）"
    )
    return "\n".join(lines)


# =====================================================
# 交互状态
#
# _pending[(group, user)] = {
#     "created_ts": float,
#     "targets": [{"openid","name","is_test"}],
#     "locked":  归一化消息条目（触发 relay 时锁定的最近消息）
# }
# =====================================================
_pending = {}


def lock_interaction(group_openid, member_openid, targets, locked):
    """发起一次转发交互，绑定 (群, 用户)，锁定最近消息与候选目标。"""
    _pending[(group_openid, member_openid)] = {
        "created_ts": time.time(),
        "targets": targets,
        "locked": locked,
    }


def get_interaction(group_openid, member_openid):
    return _pending.get((group_openid, member_openid))


def clear_interaction(group_openid, member_openid):
    _pending.pop((group_openid, member_openid), None)


def resolve_selection(group_openid, member_openid, raw, now_ts=None):
    """解析用户的序号选择。

    返回 (ok, reason_or_target)：
      - ok=True,  reason=目标 openid
      - ok=False, reason=提示文本
    超时/越界/非数字都返回 fail，并清除该交互，后续输入回到普通消息。
    """
    now = now_ts if now_ts is not None else time.time()
    inter = get_interaction(group_openid, member_openid)
    if inter is None:
        return False, "没有进行中的转发选择（请先发送 relay）"

    timeout = get_qq_fwd_select_timeout()
    if now - inter["created_ts"] > timeout:
        clear_interaction(group_openid, member_openid)
        return False, "转发选择已超时，已取消（请重新发送 relay）"

    if isinstance(raw, str):
        raw = raw.strip()
    if not isinstance(raw, str) or not raw.isdigit():
        clear_interaction(group_openid, member_openid)
        return False, "输入无效，已取消转发（请重新发送 relay）"

    idx = int(raw)
    if not (1 <= idx <= len(inter["targets"])):
        clear_interaction(group_openid, member_openid)
        return False, "选择的群不存在，已取消转发（请重新发送 relay）"

    target = inter["targets"][idx - 1]
    # 消费本次交互；锁定消息交由调用方按 inter["locked"] 使用
    return True, {"interaction": inter, "target": target}


def is_pending_interaction(group_openid, member_openid, text):
    """判断某条群消息是否为进行中的交互选择回复（在超时内）、
    以便事件层把 relay / 序号这类消息避免误缓存。"""
    inter = get_interaction(group_openid, member_openid)
    if inter is None:
        return False
    st = (text or "").strip()
    if not st.isdigit():
        return False
    if time.time() - inter["created_ts"] > get_qq_fwd_select_timeout():
        return False
    return 1 <= int(st) <= len(inter["targets"])


# =====================================================
# 媒体重发：把 QQ 附件的 URL 字节上传到目标群
#
# QQ 自己的图片/音视频 URL 国内直连即可，不走 Discord 代理。
# 下载失败时降级为文字链接（由 sender 的 media 路径兜底）。
# =====================================================
_MAX_MEDIA_BYTES = 30 * 1024 * 1024
_media_timeout = 60
_media_sema = asyncio.Semaphore(4)


_CONTENT_KIND = {
    "image": "图片",
    "audio": "音频",
    "video": "视频",
}
_SEGMENT_BUILDER = {
    "image": QQMessageSegment.file_image,
    "audio": QQMessageSegment.file_audio,
    "video": QQMessageSegment.file_video,
}


def _kind_of(content_type):
    if not content_type:
        return "file"
    return content_type.split("/", maxsplit=1)[0]


async def _fetch_no_proxy(url):
    """下载附件字节（不走代理）；失败/超限返回 None。"""
    try:
        async with _media_sema:
            async with httpx.AsyncClient(
                timeout=_media_timeout, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        logger.warning("QQ转发媒体下载失败: {} {}", resp.status_code, url)
                        return None
                    data = bytearray()
                    async for chunk in resp.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > _MAX_MEDIA_BYTES:
                            logger.warning("QQ转发媒体超上限，放弃: {}", url)
                            return None
                    return bytes(data)
    except Exception as exc:
        logger.warning("QQ转发媒体下载异常: {} {}", exc, url)
        return None


async def build_forward_content(locked):
    """把锁定的最近消息渲染为 (text_parts, media_items)。

    结构对齐 plugins.fetch.build_parts，可被 send_relay_message 直接消费：
      - 文本 → text_parts（make_text）
      - 附件 → media_items（file_* 段，下载失败自动降级为链接）
    """
    text_parts = []
    media_items = []

    text = (locked.get("content") or "").strip()
    if text:
        text_parts.append(make_text(text))

    for att in locked.get("attachments") or []:
        url = att.get("url")
        if not url:
            continue
        kind = _kind_of(att.get("content_type"))
        segment_builder = _SEGMENT_BUILDER.get(kind)
        if segment_builder is None:
            # 未知类型按文件处理
            kind = "file"
            segment_builder = _SEGMENT_BUILDER.get(kind)
        if segment_builder is not None:
            data = await _fetch_no_proxy(url)
            if data is not None:
                media_items.append({
                    "kind": _CONTENT_KIND.get(kind, "文件"),
                    "url": url,
                    "segment": segment_builder(data),
                })
                continue
        # 下载失败或类型不支持 → 交给 sender 降级为链接
        media_items.append({
            "kind": _CONTENT_KIND.get(kind, "文件"),
            "url": url,
            "segment": make_media_link(_CONTENT_KIND.get(kind, "文件"), url),
        })

    return text_parts, media_items


# =====================================================
# 发送收口
#
# custom_send 默认走 sender.send_relay_message（分片/重试/限流退避/降级）。
# 测试可替换为桩，验证“发到哪个目标群/发了什么内容”。
# 只发送到目标群，不发送到全局启用群。
# =====================================================
async def send_forward_to_group(target_openid, text_parts, media_items):
    return await custom_send(
        text_parts,
        media_items,
        groups=[target_openid],
    )


async def _default_custom_send(text_parts, media_items, groups=None):
    return await send_relay_message(text_parts, media_items, groups=groups)


custom_send = _default_custom_send  # 测试可替换


# =====================================================
# 事件处理器
# =====================================================

fwd_capture = on_message(
    priority=2,
    block=False,
)


@fwd_capture.handle()
async def handle_capture(bot: Bot, event: Event):
    """群消息缓存：记录每位用户在每群最近一条普通消息（过滤 Bot/指令）。"""
    if not isinstance(bot, QQBot):
        return
    if not isinstance(event, GroupMessageCreateEvent):
        return
    try:
        mark_recent_from_event(bot, event)
    except Exception:
        logger.exception("QQ转发捕获消息失败")


fwd_command = on_message(
    priority=15,
    block=False,
)


@fwd_command.handle()
async def handle_fwd(bot: Bot, event: Event):
    if not isinstance(bot, QQBot):
        return
    if not isinstance(event, GroupMessageCreateEvent):
        return
    text = event.get_plaintext() or ""

    # ---- relay 指令：发起交互 ----
    if text == FWD_CMD:
        await _start_interaction(bot, event)
        return

    # ---- 允许任何用户任意发消息，但只有进行中的交互选择性回复才被消费 ----
    # （此处不消费，仅当存在匹配的 pending 且内容是合法序号时由本分支处理）
    if not is_pending_interaction(event.group_openid, event.author.member_openid, text):
        return
    await _resolve_interaction(bot, event, text)


async def _start_interaction(bot, event):
    group = event.group_openid
    user = event.author.member_openid

    entries = get_qq_group_entries()
    current_is_test = next(
        (bool(e.get("is_test")) for e in entries
         if str(e.get("openid")) == group),
        False,
    )
    targets = compute_target_list(entries, group, current_is_test)

    recent = get_recent_message(group, user)
    recency_limit = get_qq_fwd_recency_limit()
    if recent is None:
        await bot.send(event, "未找到你最近的消息，请先在本群发一条消息再试")
        return
    if time.time() - recent["ts"] > recency_limit:
        await bot.send(
            event,
            f"你最近一条消息在 {recency_limit} 秒以前，已过可转发时间窗口，请重新发一条",
        )
        return
    if already_forwarded(recent.get("msg_id")):
        # 该消息此前已被转发过，提示并忽略，防止无意义重复
        await bot.send(event, "你最近那条消息已转发过，请先发一条新消息再试")
        return

    if not targets:
        await bot.send(event, "当前没有可转发的目标群（没有已启用且类型匹配的群）")
        return

    lock_interaction(group, user, targets, recent)
    menu = build_target_menu(targets, get_qq_fwd_select_timeout())
    await bot.send(event, menu)


async def _resolve_interaction(bot, event, text):
    group = event.group_openid
    user = event.author.member_openid

    ok, result = resolve_selection(group, user, text)
    if not ok:
        await bot.send(event, result)
        return

    inter = result["interaction"]
    target = result["target"]
    locked = inter["locked"]

    # 目标群实际发送前再次校验 enabled / is_test（防交互期间配置变化）
    current_is_test = bool(
        next((e.get("is_test") for e in get_qq_group_entries()
              if str(e.get("openid")) == group), False)
    )
    target_is_ok = any(
        str(e.get("openid")) == target["openid"]
        and e.get("enabled")
        and bool(e.get("is_test")) == current_is_test
        for e in get_qq_group_entries()
    )
    if not target_is_ok:
        await bot.send(event, "目标群已不可转发（配置已变化），请重新发送 relay")
        clear_interaction(group, user)
        return

    # 锁定消息可能已过窗口（发起后等待回复期间超时）→ 仍按发起时锁定发送
    text_parts, media_items = await build_forward_content(locked)

    try:
        ok_map = await send_forward_to_group(
            target["openid"], text_parts, media_items)
    except Exception as exc:
        logger.exception("QQ转发发送异常: {}", exc)
        await bot.send(event, "转发失败（发送异常，请查看日志）")
        clear_interaction(group, user)
        return

    clear_interaction(group, user)
    mark_forwarded(locked.get("msg_id"))

    if ok_map.get(target["openid"]):
        await bot.send(event, "已转发到目标群")
    else:
        await bot.send(event, "转发到目标群失败（请查看日志）")