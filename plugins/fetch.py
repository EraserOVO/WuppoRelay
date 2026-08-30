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

async def fetch_message(url):
    """按链接抓取 Discord 单条消息，返回规范化消息 dict；失败返回 None

    规范化结构：{username, channel_id, channel_name, content, embeds, attachments}
    embeds: [{title, url, image_url}]
    attachments: [{url, filename}]"""
    data = parse_discord_url(url)

    if not data:
        return None

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
            "Discord API请求异常: %s",
            exc
        )
        return None

    if response.status_code != 200:
        logger.warning(
            "Discord API错误: %s %s",
            response.status_code,
            response.text
        )
        return None

    msg = response.json()

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
        "username": username,
        "channel_id": channel_id,
        "channel_name": None,
        "content": msg.get("content", ""),
        "embeds": embeds,
        "attachments": attachments,
    }


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
            "Discord API请求异常: %s",
            exc
        )
        return None

    if response.status_code != 200:
        logger.warning(
            "Discord API错误: %s %s",
            response.status_code,
            response.text
        )
        return None

    data = response.json()

    if not isinstance(data, list) or not data:
        return None

    return str(data[0].get("id") or "")


async def fetch_channel_after_count(channel_id, after_id, limit=100):
    """统计频道里 after_id 之后的消息条数（最多统计 limit 条；
    超过时返回 limit + 1 表示“至少 limit 条”）；失败返回 0"""
    api = (
        f"https://discord.com/api/v10/"
        f"channels/{channel_id}/messages"
        f"?after={after_id}&limit={limit}"
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
            "Discord API请求异常: %s",
            exc
        )
        return 0

    if response.status_code != 200:
        logger.warning(
            "Discord API错误: %s %s",
            response.status_code,
            response.text
        )
        return 0

    data = response.json()

    if not isinstance(data, list):
        return 0

    if len(data) >= limit:
        return limit + 1

    return len(data)


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

    return {
        "username": username,
        "channel_id": str(event.channel_id),
        "channel_name": channel_name,
        "content": getattr(event, "content", "") or "",
        "embeds": embeds,
        "attachments": attachments,
    }


# =====================================================
# 规范化消息 → QQ 消息段（唯一一份渲染逻辑）
# =====================================================

async def build_parts(message):
    """规范化消息 → (text_parts, media_items, has_content)

    - header（来源频道/作者）始终在 text_parts 首位
    - 正文 + Emoji、embeds、附件统一渲染，媒体并发下载
    - 下载失败降级为文字 + 原链接
    - has_content 用于实时转发跳过空消息（仅 header 时）"""
    text_parts = []
    media_items = []

    channel_name = message.get("channel_name") or message.get("channel_id") or "?"
    username = message.get("username") or "unknown"

    text_parts.append(
        make_text(
            f"自动转发来自\n"
            f"Discord [#{channel_name}] 中 [{username}]的消息："
        )
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
