# -*- coding: utf-8 -*-
"""QQ→QQ 手动转发。

任何 QQ 用户在一个已注册的 QQ 接收群里：

  relay-list                  列出当前可作为转发目标的、已注册且 enabled 的 QQ 群（带序号）
  relay {序号}                将用户接下来发送的下一条普通消息，转发到序号对应的目标群

发送前会再次校验目标群（仍注册 / enabled / 测试组隔离 / 排除当前群），
配置变化导致失效时回复「转发失败」并结束本次交互。等待下一条消息期间绑定
(当前群, 发送者 member_openid)，防止多人串台；超过 qq_fwd_select_timeout 秒仍未收到
下一条可转发消息时静默清除 pending（不发送任何提示）。

实现要点：
  - 不再依赖“上一条消息”缓存：转发的就是 relay {序号} 之后收到的下一条普通用户消息。
  - 测试隔离完全由「测试组」实现：当前群在测试组内 → 只能转发给同为测试组成员的群；
    否则只能转发给非测试组成员的群。不再读取群条目的 is_test 属性。
  - 过滤 Bot 消息（author.bot == True），relay / relay-list 等指令本身不会进入转发内容。
  - 发送复用 plugins/sender（分片/重试/限流退避/媒体降级）与 plugins/media（图片/音视频字节上传+链接降级）。
  - 不依赖白名单、不依赖转发组（forwarding_groups 只表达 Discord频道→QQ群 路由；
    此处仅复用其中的「测试组」做测试隔离）。
"""
import asyncio
import time

import httpx

from nonebot import logger
from nonebot import on_message
from nonebot import get_driver
from nonebot.adapters import Bot, Event

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import MessageSegment as QQMessageSegment
from nonebot.adapters.qq.event import GroupMessageCreateEvent

from plugins.config import (
    get_qq_group_entries,
    get_qq_fwd_select_timeout,
    get_test_group_openids,
)
from plugins.media import make_text, make_media_link
from plugins.sender import send_relay_message


# =====================================================
# 指令
# =====================================================
CMD_LIST = "relay-list"
CMD_PREFIX = "relay "
CMD_BARE = "relay"


# =====================================================
# 目标群计算（纯函数）
#
# 候选 = 已注册(enabled) 且 测试组成员关系与当前群一致 且 非当前群。
# 独立于 forwarding_groups（仅复用「测试组」成员做测试隔离）。
# =====================================================

def compute_target_list(entries, current_openid, test_openids):
    """从群条目列表计算可转发目标群。

    test_openids：测试组内的群 openid 集合（set 或可 in 判断的容器）。
    返回 [{"openid", "name", "in_test_group"}]；只含 enabled、
    与当前群测试组成员关系一致且非当前群。
    顺序与 settings 中条目顺序一致，relay-list / relay {序号} 共用同一顺序。
    """
    current = str(current_openid)
    current_test = current in set(test_openids or ())
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
        in_test = oid in test_openids
        if in_test != current_test:
            continue
        targets.append({
            "openid": oid,
            "name": str(item.get("name") or item.get("remark") or "").strip(),
            "in_test_group": in_test,
        })
    return targets


def build_relay_list(targets):
    """把目标群列表渲染为 relay-list 的回复文本（带连续序号）。"""
    if not targets:
        return "【Relay群列表】\n当前没有可转发的目标群"
    lines = ["【Relay群列表】"]
    for i, t in enumerate(targets, start=1):
        label = t["name"] or t["openid"]
        mark = "（测试）" if t["in_test_group"] else ""
        lines.append(f"{i}. {label}{mark}")
    return "\n".join(lines)


# =====================================================
# 指令解析（纯函数）
#
# parse_relay 返回 (kind, payload)：
#   ("list",     None)   —— relay-list
#   ("number",   int)    —— relay {i}，i 为 >=1 的整数
#   ("invalid",  None)   —— relay / relay <非数字>，无法定位目标群
#   (None,       None)   —— 不是 relay 指令
# =====================================================

def parse_relay(text):
    st = (text or "").strip()
    if st == CMD_LIST:
        return ("list", None)
    if st == CMD_BARE:
        return ("invalid", None)
    if st.startswith(CMD_PREFIX):
        arg = st[len(CMD_PREFIX):].strip()
        if arg.isdigit():
            return ("number", int(arg))
        return ("invalid", None)
    return (None, None)


def resolve_target_by_number(entries, current_openid, test_openids, number):
    """relay {序号} → 目标群；序号越界返回 None。"""
    if number < 1:
        return None
    targets = compute_target_list(entries, current_openid, test_openids)
    if number > len(targets):
        return None
    return targets[number - 1]


# =====================================================
# Pending 状态
#
# _pending[(group_openid, member_openid)] = {
#     "created_ts": float,
#     "target": {"openid","name","in_test_group"},
# }
# 绑定 (群, 用户)，超时由 qq_fwd_select_timeout 决定。
# =====================================================
_pending = {}


def has_pending(group_openid, member_openid):
    return (group_openid, member_openid) in _pending


def get_pending(group_openid, member_openid):
    return _pending.get((group_openid, member_openid))


def set_pending(group_openid, member_openid, target):
    _pending[(group_openid, member_openid)] = {
        "created_ts": time.time(),
        "target": target,
    }


def clear_pending(group_openid, member_openid):
    _pending.pop((group_openid, member_openid), None)


def is_pending_expired(entry, timeout, now_ts=None):
    now = now_ts if now_ts is not None else time.time()
    return now - entry["created_ts"] > timeout


def collect_expired(now_ts):
    """返回所有已超时的 pending 键；只收集不删除（由调用方决定如何处理）。"""
    timeout = get_qq_fwd_select_timeout()
    return [
        key for key, entry in _pending.items()
        if is_pending_expired(entry, timeout, now_ts)
    ]


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


def normalize_event_message(event):
    """把一条群消息归一化为可转发内容 dict（文本 + 附件）。

    结构对齐 build_forward_content 的入参，供待转发的“下一条消息”使用。
    """
    content = event.get_plaintext() or ""
    attachments = [
        {"url": a.url, "content_type": a.content_type or ""}
        for a in (event.attachments or [])
        if getattr(a, "url", None)
    ]
    return {"content": content, "attachments": attachments}


async def build_forward_content(message):
    """把一条待转发消息渲染为 (text_parts, media_items)。

    结构对齐 plugins.fetch.build_parts，可被 send_relay_message 直接消费：
      - 文本 → text_parts（make_text）
      - 附件 → media_items（file_* 段，下载失败自动降级为链接）
    """
    text_parts = []
    media_items = []

    text = (message.get("content") or "").strip()
    if text:
        text_parts.append(make_text(text))

    for att in message.get("attachments") or []:
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


# 目标群实际发送前再次校验（测试组隔离由 test_openids 决定，不读 is_test 属性）
def _target_still_valid(target, group, entries, test_openids):
    test_openids = set(test_openids or ())
    current_test = str(group) in test_openids
    return any(
        str(e.get("openid")) == target["openid"]
        and e.get("enabled")
        and (str(e.get("openid")) in test_openids) == current_test
        for e in entries
    )


# =====================================================
# 事件处理器
# =====================================================

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

    kind, payload = parse_relay(text)

    if kind == "list":
        await _cmd_relay_list(bot, event)
        return
    if kind == "number":
        await _cmd_relay_number(bot, event, payload)
        return
    if kind == "invalid":
        await bot.send(event, "无法找到对应的转发群")
        return

    # 普通消息：若有该用户的 pending，则作为「下一条消息」转发
    await _try_forward_next(bot, event, text)


async def _cmd_relay_list(bot, event):
    group = event.group_openid
    entries = get_qq_group_entries()
    test_openids = get_test_group_openids()
    targets = compute_target_list(entries, group, test_openids)
    await bot.send(event, build_relay_list(targets))


async def _cmd_relay_number(bot, event, number):
    group = event.group_openid
    user = event.author.member_openid
    entries = get_qq_group_entries()
    test_openids = get_test_group_openids()
    target = resolve_target_by_number(entries, group, test_openids, number)
    if target is None:
        await bot.send(event, "无法找到对应的转发群")
        return
    set_pending(group, user, target)
    return


async def _try_forward_next(bot, event, text):
    group = event.group_openid
    user = event.author.member_openid
    entry = get_pending(group, user)
    if entry is None:
        return  # 没有进行中的转发，静默

    timeout = get_qq_fwd_select_timeout()
    if is_pending_expired(entry, timeout):
        # 超时：只清除 pending，不发任何消息
        clear_pending(group, user)
        return

    # 消费 pending：本消息即转发内容
    clear_pending(group, user)
    message = normalize_event_message(event)

    # 实际发送前再次校验目标群
    if not _target_still_valid(
        entry["target"], group,
        get_qq_group_entries(), get_test_group_openids(),
    ):
        await bot.send(event, "转发失败")
        return

    text_parts, media_items = await build_forward_content(message)
    try:
        ok_map = await send_forward_to_group(
            entry["target"]["openid"], text_parts, media_items)
    except Exception as exc:
        logger.exception("QQ转发发送异常: {}", exc)
        await bot.send(event, "转发失败")
        return

    if ok_map.get(entry["target"]["openid"]):
        await bot.send(event, "转发成功")
    else:
        await bot.send(event, "转发失败")


# =====================================================
# 超时清扫：用户在 relay {序号} 后一直不发送内容时，超时静默清除 pending
# （不发送任何提示消息）。
# 仅在能拿到 QQ Bot 时启动一次后台任务。
# =====================================================
_timeout_loop_started = False


async def expire_pendings(now_ts):
    """清除所有已超时的 pending（不发任何消息）。

    供超时清扫任务与测试复用。"""
    for key in collect_expired(now_ts):
        clear_pending(key[0], key[1])


async def _timeout_loop():
    while True:
        try:
            await asyncio.sleep(2)
            await expire_pendings(time.time())
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("QQ转发超时清扫异常")


@get_driver().on_bot_connect
async def _start_timeout_loop(bot: Bot):
    global _timeout_loop_started
    if not isinstance(bot, QQBot):
        return
    if _timeout_loop_started:
        return
    _timeout_loop_started = True
    asyncio.create_task(_timeout_loop())