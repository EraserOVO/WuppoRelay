import asyncio
import os
import re

import httpx

from nonebot import logger
from nonebot.adapters.qq import MessageSegment as QQMessageSegment

from plugins.config import get_discord_token
from plugins.media import (
    AUDIO_EXTS,
    IMAGE_EXTS,
    _get_proxy,
    convert_mentions,
    convert_mention_ids,
    cleanup_markdown,
    parse_discord_emoji,
    make_text,
    make_file_link,
    make_media_link,
    fetch_bytes_many,
)


# =====================================================
# Discord 消息统一抓取 / 解析 / 渲染
#
# 单一职责：Discord 消息 → QQ 消息段 (text_parts, media_items)。
# 两条数据来源（实时事件 / REST 单条抓取）先归一为同一种
# 规范化消息 dict，再走同一份渲染逻辑 build_parts()：
#   - relay.py 用 normalize_event() 归一事件对象
#   - command.py 用 fetch_message() 按链接抓取
# 媒体下载统一走 media.py 的并发限流。
# =====================================================

DISCORD_API_TIMEOUT = 20

# =====================================================
# 补发抓取分页参数（仅 fetch_channel_messages_after 使用）
#
# - FETCH_PAGE_INTERVAL：翻页请求间隔秒数，避免大缺口连续请求过快触发限流
# - FETCH_COLLECT_CAP：单次收集消息总量上限，防止极大缺口无限占用内存；
#   未确认翻到缺口底部就超限时返回 []（本轮不补发），绝不返回不完整
#   列表让补发游标越过更旧的缺口消息
# - RATE_LIMIT_ATTEMPTS / RATE_LIMIT_FALLBACK：429 重试次数与
#   Retry-After 缺失/非法时的默认退避秒数
# =====================================================

FETCH_PAGE_INTERVAL = 0.5
FETCH_COLLECT_CAP = 1000
RATE_LIMIT_ATTEMPTS = 3
RATE_LIMIT_FALLBACK = 5.0

# 回复引用行正文显示上限（防止被回复的长文刷屏）
REFERENCE_TEXT_LIMIT = 50


def _rate_limit_wait(response, fallback):
    """从 429 响应头取 Retry-After（秒）；缺失/非法时用 fallback"""
    try:
        raw = response.headers.get("retry-after")
        if raw:
            return max(float(raw), fallback)
    except Exception:
        pass
    return fallback


def parse_discord_url(url):
    """解析 Discord 消息链接，返回 {guild_id, channel_id, message_id}"""
    pattern = (
        r"discord\.com/channels/"
        r"(\d+)/(\d+)/(\d+)"
    )

    result = re.search(
        pattern,
        url
    )

    if not result:
        return None

    return {
        "guild_id": result.group(1),
        "channel_id": result.group(2),
        "message_id": result.group(3),
    }


def is_discord_url(text):
    """判断文本是否为 Discord 消息链接（http 前缀）"""
    return re.match(
        r"https://discord\.com/channels/\d+/\d+/\d+",
        text
    )


# =====================================================
# REST 抓取（按链接拉单条消息）
# =====================================================

def _discord_id(value):
    """Discord ID → str；None/UNSET 哨兵 → 空串"""
    if value is None:
        return ""
    if type(value).__name__ == "_UNSET":
        return ""
    return str(value)


def _extract_reference(ref_obj, referenced=None):
    """从 API 消息对象提取回复引用 → 可选 reference dict

    内联 referenced_message（含作者+正文）优先；只有 message_reference
    时仅带 message_id/channel_id，由 build_parts 阶段回退抓取补全。
    返回 {message_id, channel_id, username, content} 或 None"""
    if not isinstance(ref_obj, dict):
        ref_obj = {}
    if not isinstance(referenced, dict):
        referenced = {}

    message_id = referenced.get("id") or ref_obj.get("message_id")
    if not message_id:
        return None

    author = referenced.get("author") or {}

    return {
        "message_id": _discord_id(message_id),
        "channel_id": _discord_id(
            ref_obj.get("channel_id")
            or referenced.get("channel_id")
        ),
        "username": (
            author.get("global_name")
            or author.get("username")
            or ""
        ),
        "content": referenced.get("content") or "",
    }


def normalize_api_message(msg):
    """Discord API 消息对象 → 规范化 dict（含 message_id）

    供 fetch_message / fetch_channel_messages_after 复用。
    结构：{message_id, username, channel_id, channel_name, content,
           embeds, attachments, reference}
    reference 可选：{message_id, channel_id, username, content}
    或 None（非回复消息）。
    embeds: [{title, url, image_url}]
    attachments: [{url, filename}]"""
    author = msg.get(
        "author",
        {}
    ) or {}

    username = (
        author.get("global_name")
        or author.get("username")
        or "unknown"
    )

    embeds = []
    for embed in msg.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        image = embed.get("image") or {}
        image_url = image.get("url") if isinstance(image, dict) else None
        embeds.append({
            "title": embed.get("title"),
            "url": embed.get("url"),
            "image_url": image_url,
        })

    attachments = []
    for att in msg.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        url = att.get("url")
        if not url:
            continue
        attachments.append({
            "url": url,
            "filename": att.get("filename", ""),
        })

    return {
        "message_id": str(msg.get("id") or ""),
        "author_username": author.get("username") or "",
        "username": username,
        "channel_id": str(msg.get("channel_id") or ""),
        "channel_name": None,
        "content": msg.get("content", ""),
        "embeds": embeds,
        "attachments": attachments,
        "reference": _extract_reference(
            msg.get("message_reference"),
            msg.get("referenced_message"),
        ),
    }


async def fetch_message(url):
    """按链接抓取 Discord 单条消息，返回 (规范化消息 dict, 失败原因 or None)

    失败时消息为 None，原因可用于提示用户（如 HTTP 403/404/429）。
    规范化结构：{username, channel_id, channel_name, content, embeds, attachments}
    embeds: [{title, url, image_url}]
    attachments: [{url, filename}]"""
    data = parse_discord_url(url)

    if not data:
        return None, "链接格式无法解析"

    channel_id = data["channel_id"]
    message_id = data["message_id"]

    api = (
        f"https://discord.com/api/v10/"
        f"channels/{channel_id}/messages/{message_id}"
    )

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    try:
        async with httpx.AsyncClient(
            proxy=_get_proxy(),
            timeout=DISCORD_API_TIMEOUT,
        ) as client:
            response = await client.get(
                api,
                headers=headers,
            )
    except Exception as exc:
        logger.warning(
            "Discord API请求异常: {}",
            exc
        )
        return None, "请求异常"

    if response.status_code != 200:
        logger.warning(
            "Discord API错误: {} {}",
            response.status_code,
            response.text
        )
        return None, f"HTTP {response.status_code}"

    message = normalize_api_message(
        response.json()
    )

    # 链接解析出的频道 ID 为准（与 API 返回应一致，保险起见覆盖）
    message["channel_id"] = channel_id

    return message, None


async def fetch_channel_messages_after(channel_id, after_id, limit=10):
    """抓取频道中 after_id 之后的消息，返回最旧的 limit 条（时间正序 旧→新）

    Discord API 单页上限 100 条且按 id 倒序返回，因此翻页到底收集全部
    缺口后取最旧 limit 条，保证补发从旧消息开始、不丢旧消息。

    防漏发安全规则（补发游标只按已发送消息推进，返回不完整列表会让
    游标越过更旧的缺口消息造成永久漏发，因此）：
    - 任一页请求异常/超时/非 200/429 重试耗尽/响应解析失败/翻页锚点缺失
      → 丢弃已收集的部分结果，返回 []，宁可下一轮重新补发
    - 收集量超过 FETCH_COLLECT_CAP 且未确认翻到缺口底部 → 返回 []，
      本轮不补发（极大缺口如确认放弃，可在面板"清除待补发"）
    - 仅当确认翻到缺口底部（短页/空页）才返回收集结果"""
    if limit <= 0:
        return []

    page_limit = 100

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    collected = []
    before = None

    while True:

        api = (
            f"https://discord.com/api/v10/"
            f"channels/{channel_id}/messages"
            f"?after={after_id}&limit={page_limit}"
        )

        if before:
            api += f"&before={before}"

        # 单页请求：429 按 Retry-After 退避重试；其余失败直接放弃本轮
        response = None
        rate_limited = False

        for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):

            try:
                async with httpx.AsyncClient(
                    proxy=_get_proxy(),
                    timeout=DISCORD_API_TIMEOUT,
                ) as client:
                    response = await client.get(
                        api,
                        headers=headers,
                    )
            except Exception as exc:
                logger.warning(
                    "Discord API请求异常，本轮补发放弃: {}",
                    exc
                )
                return []

            if response.status_code != 429:
                break

            rate_limited = True

            if attempt == RATE_LIMIT_ATTEMPTS:
                break

            wait = _rate_limit_wait(
                response,
                RATE_LIMIT_FALLBACK,
            )

            logger.warning(
                "Discord API限流(429)，{}s 后重试({}/{}): {}",
                wait,
                attempt,
                RATE_LIMIT_ATTEMPTS - 1,
                channel_id,
            )

            await asyncio.sleep(wait)

        if response.status_code != 200:

            if rate_limited:
                logger.warning(
                    "Discord API限流重试耗尽，本轮补发放弃: {}",
                    channel_id
                )
            else:
                logger.warning(
                    "Discord API错误: {} {}",
                    response.status_code,
                    response.text
                )

            # 丢弃部分结果：返回不完整列表会让补发游标越过
            # 更旧的缺口消息（永久漏发），宁可下一轮重新补发
            return []

        try:
            data = response.json()
        except Exception as exc:
            logger.warning(
                "Discord API响应解析失败，本轮补发放弃: {}",
                exc
            )
            return []

        if not isinstance(data, list) or not data:
            break

        collected.extend(data)

        if len(data) < page_limit:
            # 短页 = 已翻到缺口底部，收集完整
            break

        if len(collected) > FETCH_COLLECT_CAP:
            # 缺口超出收集上限且底部未确认：无法保证不含更旧消息，
            # 本轮安全结束不补发，避免游标越过未抓取的旧消息
            logger.warning(
                "补发抓取: 频道{}缺口超过收集上限({}条)，本轮不补发"
                "（如确认放弃缺口可在面板清除待补发）",
                channel_id,
                FETCH_COLLECT_CAP,
            )
            return []

        # 本页取满，翻页取更早的（仍晚于 after_id）消息
        before = str(data[-1].get("id") or "")

        if not before:
            # 整页却拿不到翻页锚点，同样无法确认缺口底部，按失败处理
            logger.warning(
                "补发抓取: 翻页锚点缺失，本轮补发放弃: {}",
                channel_id
            )
            return []

        # 页间小延迟：大缺口连续请求过快容易触发 429
        await asyncio.sleep(FETCH_PAGE_INTERVAL)

    if not collected:
        return []

    # 全部缺口（新→旧），反转成旧→新后取最旧 limit 条
    messages = [
        normalize_api_message(msg)
        for msg in collected
    ]
    messages.reverse()

    return messages[:limit]


async def fetch_channel_latest(channel_id):
    """返回频道最新一条消息 ID（REST，走代理）；失败返回 None"""
    api = (
        f"https://discord.com/api/v10/"
        f"channels/{channel_id}/messages?limit=1"
    )

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    try:
        async with httpx.AsyncClient(
            proxy=_get_proxy(),
            timeout=DISCORD_API_TIMEOUT,
        ) as client:
            response = await client.get(
                api,
                headers=headers,
            )
    except Exception as exc:
        logger.warning(
            "Discord API请求异常: {}",
            exc
        )
        return None

    if response.status_code != 200:
        logger.warning(
            "Discord API错误: {} {}",
            response.status_code,
            response.text
        )
        return None

    data = response.json()

    if not isinstance(data, list) or not data:
        return None

    return str(data[0].get("id") or "")


async def fetch_channel_name(channel_id):
    """REST 获取频道名称；失败返回 None"""
    api = f"https://discord.com/api/v10/channels/{channel_id}"

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    try:
        async with httpx.AsyncClient(
            proxy=_get_proxy(),
            timeout=DISCORD_API_TIMEOUT,
        ) as client:
            response = await client.get(
                api,
                headers=headers,
            )
    except Exception as exc:
        logger.warning(
            "Discord API请求异常: {}",
            exc
        )
        return None

    if response.status_code != 200:
        logger.warning(
            "Discord API错误: {} {}",
            response.status_code,
            response.text
        )
        return None

    data = response.json()

    if not isinstance(data, dict):
        return None

    name = data.get("name")

    return str(name) if name else None


async def fetch_channel_gap_count(channel_id, after_id, cap=1000):
    """统计频道中 after_id 之后的消息条数（最多统计 cap 条，
    超过时返回 cap 表示"至少 cap 条"）；失败返回 None"""
    page_limit = 100

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    total = 0
    before = None

    while True:

        api = (
            f"https://discord.com/api/v10/"
            f"channels/{channel_id}/messages"
            f"?after={after_id}&limit={page_limit}"
        )

        if before:
            api += f"&before={before}"

        try:
            async with httpx.AsyncClient(
                proxy=_get_proxy(),
                timeout=DISCORD_API_TIMEOUT,
            ) as client:
                response = await client.get(
                    api,
                    headers=headers,
                )
        except Exception as exc:
            logger.warning(
                "Discord API请求异常: {}",
                exc
            )
            return None

        if response.status_code != 200:
            logger.warning(
                "Discord API错误: {} {}",
                response.status_code,
                response.text
            )
            return None

        data = response.json()

        if not isinstance(data, list) or not data:
            break

        total += len(data)

        if total >= cap:
            return cap

        if len(data) < page_limit:
            break

        before = str(data[-1].get("id") or "")

        if not before:
            break

    return total


# =====================================================
# 事件对象归一（实时转发路径）
# =====================================================

def normalize_event(event, channel_name):
    """把 Discord 事件对象转成与 fetch_message 相同的规范化 dict"""
    author = getattr(event, "author", None)
    if author:
        # 与 fetch_message 保持一致：优先 global_name（昵称），缺失时回退 username
        username = (
            getattr(author, "global_name", None)
            or author.username
            or "unknown"
        )
    else:
        username = "unknown"

    embeds = []
    for embed in getattr(event, "embeds", []):
        image = getattr(embed, "image", None)
        embeds.append({
            "title": getattr(embed, "title", None),
            "url": getattr(embed, "url", None),
            "image_url": (
                getattr(image, "url", None)
                if image
                else None
            ),
        })

    attachments = []
    for file in getattr(event, "attachments", []):
        url = getattr(file, "url", None)
        if not url:
            continue
        attachments.append({
            "url": url,
            "filename": getattr(file, "filename", ""),
        })

    # 事件只携带被引用消息的 ID，无内联原文；
    # 由 build_parts 阶段按 ID 回退抓取一次补全
    ref = getattr(event, "message_reference", None)
    reference = None
    if (
        ref is not None
        and type(ref).__name__ != "_UNSET"
        and _discord_id(getattr(ref, "message_id", None))
    ):
        reference = {
            "message_id": _discord_id(getattr(ref, "message_id", None)),
            "channel_id": _discord_id(getattr(ref, "channel_id", None)),
            "username": "",
            "content": "",
        }

    return {
        "username": username,
        "channel_id": str(event.channel_id),
        "channel_name": channel_name,
        "content": getattr(event, "content", "") or "",
        "embeds": embeds,
        "attachments": attachments,
        "reference": reference,
    }


# =====================================================
# 频道显示名解析（带进程内缓存）
#
# 实时转发/补发/私聊链接共用同一兜底：配置里没填名字时
# 查一次 Discord API 真实名并缓存，避免高频路径每条消息
# 都请求一次 Discord。
# =====================================================

_channel_name_cache = {}


async def _resolve_channel_name(channel_id):
    """查 Discord 真实频道名，进程内缓存；失败返回 None（调用方回退 ID）"""
    if channel_id in _channel_name_cache:
        return _channel_name_cache[channel_id]

    name = await fetch_channel_name(channel_id)

    _channel_name_cache[channel_id] = name

    return name


# =====================================================
# 回复引用行（Discord 回复 → "↩ 用户名：被回复内容"）
#
# 实时转发 / 启动补发 / 私聊链接共用 build_parts 渲染：
#   - REST 路径由 normalize_api_message 优先读内联 referenced_message
#   - 事件路径只有 message_reference.message_id，回退抓取一次原文
# 抓取失败或无可引用正文时静默跳过引用行，不影响当前消息转发。
# =====================================================

async def _fetch_reference_message(channel_id, message_id):
    """按 ID 抓取被引用消息原文；失败返回 None（引用行 best-effort）"""
    if not channel_id or not message_id:
        return None

    api = (
        f"https://discord.com/api/v10/"
        f"channels/{channel_id}/messages/{message_id}"
    )

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    try:
        async with httpx.AsyncClient(
            proxy=_get_proxy(),
            timeout=DISCORD_API_TIMEOUT,
        ) as client:
            response = await client.get(
                api,
                headers=headers,
            )
    except Exception:
        return None

    if response.status_code != 200:
        return None

    try:
        data = response.json()
    except Exception:
        return None

    return data if isinstance(data, dict) else None


async def _resolve_reference(reference, fallback_channel_id):
    """补全引用原文供渲染；只有 ID 时回退抓取一次。

    返回带 username/content 的 reference；无可引用正文返回 None。
    抓取失败静默返回 None，不影响当前消息转发。"""
    if not reference:
        return None

    if (reference.get("content") or "").strip():
        return reference

    if reference.get("username"):
        # 有作者但无正文（如图片回复），没有可引用的文字
        return None

    message_id = reference.get("message_id")
    channel_id = reference.get("channel_id") or fallback_channel_id

    if not message_id or not channel_id:
        return None

    raw = await _fetch_reference_message(
        channel_id,
        message_id,
    )

    if not raw:
        return None

    author = raw.get("author") or {}

    reference["username"] = (
        author.get("global_name")
        or author.get("username")
        or ""
    )
    reference["content"] = raw.get("content") or ""

    if not (reference.get("content") or "").strip():
        return None

    return reference


def _render_reference(reference):
    """引用行文本：↩ 用户名：被回复内容（正文截断、压平换行）"""
    content = (reference.get("content") or "").strip()

    if not content:
        return None

    username = reference.get("username") or "unknown"

    content = convert_mentions(content)
    content, _ = parse_discord_emoji(content)
    content = convert_mention_ids(content)
    content = cleanup_markdown(content)
    content = re.sub(r"\s+", " ", content).strip()

    if not content:
        return None

    if len(content) > REFERENCE_TEXT_LIMIT:
        content = content[:REFERENCE_TEXT_LIMIT] + "…"

    return "↩︎ " + username + "：" + content


# =====================================================
# 规范化消息 → QQ 消息段（唯一一份渲染逻辑）
# =====================================================

async def build_parts(message, source_label="自动转发来自"):
    """规范化消息 → (text_parts, media_items, has_content)

    - header（来源频道/作者）始终在 text_parts 首位
    - 回复引用行（↩ 用户名：被回复内容）位于 header 后、正文前；
      抓取失败时静默跳过，不进 has_content
    - 正文 + Emoji、embeds、附件统一渲染，媒体并发下载
    - 下载失败降级为文字 + 原链接
    - has_content 用于实时转发跳过空消息（仅 header 时）
    - source_label 区分消息来源（实时转发 / 启动补发）"""
    text_parts = []
    media_items = []

    channel_name = message.get("channel_name") or ""
    channel_id = message.get("channel_id") or ""

    if not channel_name and channel_id:
        # 未配置显示名时兜底：查 Discord API 真实名（每频道缓存一次）
        channel_name = await _resolve_channel_name(channel_id)

    channel_name = channel_name or channel_id or "?"
    username = message.get("username") or "unknown"

    text_parts.append(
        make_text(
            f"{source_label}\n"
            f"Discord [#{channel_name}] 中 [{username}]的消息："
        )
    )

    # ======================
    # 回复引用行（best-effort，失败不影响当前消息转发）
    # ======================

    reference = await _resolve_reference(
        message.get("reference"),
        channel_id,
    )

    if reference:
        ref_text = _render_reference(reference)
        if ref_text:
            text_parts.append(
                make_text("\n" + ref_text)
            )

    has_content = False

    # ======================
    # 普通文字 + Emoji
    # ======================

    raw_content = message.get("content") or ""
    content = raw_content

    if content:
        content = convert_mentions(content)
        content, emojis = parse_discord_emoji(content)
        content = convert_mention_ids(content)
        content = cleanup_markdown(content)

        if content:
            text_parts.append(
                make_text("\n" + content)
            )
            has_content = True

        if emojis:
            emoji_bytes_map = await fetch_bytes_many(emojis)
            for emoji in emojis:
                emoji_bytes = emoji_bytes_map.get(emoji)
                if emoji_bytes:
                    media_items.append({
                        "kind": "表情",
                        "url": emoji,
                        "segment": QQMessageSegment.file_image(
                            emoji_bytes,
                            "emoji.gif" if emoji.endswith(".gif") else "emoji.png"
                        ),
                    })
                else:
                    text_parts.append(
                        make_media_link("表情", emoji)
                    )
                has_content = True

    # ======================
    # Embed
    # ======================

    for embed in message.get("embeds") or []:
        title = embed.get("title")
        url = embed.get("url")
        image_url = embed.get("image_url")

        if title:
            text_parts.append(
                make_text("\n\n【" + title + "】")
            )
            has_content = True

        if url and not raw_content:
            text_parts.append(
                make_text("\n" + url)
            )
            has_content = True

        if image_url:
            image_bytes_map = await fetch_bytes_many([image_url])
            image_bytes = image_bytes_map.get(image_url)
            if image_bytes:
                media_items.append({
                    "kind": "图片",
                    "url": image_url,
                    "segment": QQMessageSegment.file_image(
                        image_bytes,
                        "embed.png"
                    ),
                })
            else:
                text_parts.append(
                    make_media_link("图片", image_url)
                )
            has_content = True

    # ======================
    # 附件
    # ======================

    image_jobs = []
    audio_jobs = []

    for att in message.get("attachments") or []:
        url = att.get("url")
        filename = att.get("filename") or ""
        if not url:
            continue

        ext = os.path.splitext(filename.lower())[1]

        if ext in IMAGE_EXTS:
            image_jobs.append((url, filename))
        elif ext in AUDIO_EXTS:
            audio_jobs.append((url, filename))
        else:
            text_parts.append(
                make_file_link(filename, url)
            )
            has_content = True

    if image_jobs:
        image_bytes_map = await fetch_bytes_many(
            [url for url, _ in image_jobs]
        )
        for url, filename in image_jobs:
            media_bytes = image_bytes_map.get(url)
            if media_bytes:
                media_items.append({
                    "kind": "图片",
                    "url": url,
                    "segment": QQMessageSegment.file_image(
                        media_bytes,
                        filename
                    ),
                })
            else:
                text_parts.append(
                    make_media_link("图片", url)
                )
            has_content = True

    if audio_jobs:
        audio_bytes_map = await fetch_bytes_many(
            [url for url, _ in audio_jobs]
        )
        for url, filename in audio_jobs:
            media_bytes = audio_bytes_map.get(url)
            if media_bytes:
                media_items.append({
                    "kind": "音频",
                    "url": url,
                    "segment": QQMessageSegment.file_audio(
                        media_bytes,
                        filename
                    ),
                })
            else:
                text_parts.append(
                    make_media_link("音频", url)
                )
            has_content = True

    return text_parts, media_items, has_content
