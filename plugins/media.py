import asyncio
import re
import threading

import httpx

from nonebot import logger
from nonebot.adapters.qq import MessageSegment as QQMessageSegment


# =====================================================
# Discord 文本/Emoji 处理 + QQ 消息段构建 + 媒体下载
#
# 单一职责：不依赖任何事件对象，只做纯转换与字节下载。
# 供 relay.py / command.py 等插件复用。
# =====================================================

IMAGE_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp"
}

AUDIO_EXTS = {
    ".wav",
    ".mp3",
    ".ogg",
    ".flac",
    ".m4a"
}


# =====================================================
# Discord 文本美化（B2）
#
# 把 Discord Markdown 转成 QQ 里可读的纯文本：
#   - @everyone / @here → 可读文本
#   - <@用户> <@&角色> <#频道> 提及 ID → 可读文本（不查名字，
#     避免每提及一次就多一次 API 调用）
#   - 粗体/斜体/删除线/行内代码/代码块围栏 → 去掉标记
#   - [文字](链接) → 文字（链接）
# =====================================================

def convert_mentions(text: str):

    if not text:
        return ""

    text = text.replace(
        "@everyone",
        "[@全体成员]"
    )

    text = text.replace(
        "@here",
        "[@在线成员]"
    )

    return text


def convert_mention_ids(text: str):

    if not text:
        return ""

    text = re.sub(
        r"<@!?(\d+)>",
        r"[@\1]",
        text
    )

    text = re.sub(
        r"<@&(\d+)>",
        r"[角色@\1]",
        text
    )

    text = re.sub(
        r"<#(\d+)>",
        r"[#\1]",
        text
    )

    return text


def cleanup_markdown(text: str):

    if not text:
        return ""

    # 代码块围栏 ```lang ... ```（去掉围栏行，保留内容）
    text = re.sub(
        r"```[^\n]*\n?",
        "",
        text
    )

    # 行内代码
    text = re.sub(
        r"`([^`\n]+)`",
        r"\1",
        text
    )

    # 粗体
    text = re.sub(
        r"\*\*([^*\n]+)\*\*",
        r"\1",
        text
    )

    # 斜体（前后非单词字符，避免误伤 1*2*3 之类）
    text = re.sub(
        r"(?<!\w)\*([^*\n]+)\*(?!\w)",
        r"\1",
        text
    )

    # 删除线
    text = re.sub(
        r"~~([^~\n]+)~~",
        r"\1",
        text
    )

    # [文字](链接) → 文字（链接）
    text = re.sub(
        r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)",
        r"\1（\2）",
        text
    )

    return text


def parse_discord_emoji(text: str):

    emoji_urls = []

    if not text:
        return "", emoji_urls


    def static_replace(match):

        emoji_urls.append(
            f"https://cdn.discordapp.com/emojis/{match.group(2)}.png"
        )

        return ""


    def animated_replace(match):

        emoji_urls.append(
            f"https://cdn.discordapp.com/emojis/{match.group(2)}.gif"
        )

        return ""


    text = re.sub(
        r"<:([^:]+):(\d+)>",
        static_replace,
        text
    )


    text = re.sub(
        r"<a:([^:]+):(\d+)>",
        animated_replace,
        text
    )


    return text.strip(), emoji_urls


def make_text(text):

    return QQMessageSegment.text(text)


def make_file_link(filename, url):

    return make_text(
        f"\n[文件: {filename}]\n{url}"
    )


def make_media_link(kind, url):

    return make_text(
        f"\n[{kind}] {url}"
    )


def _get_proxy():
    """读取 NoneBot 配置中的代理（Discord CDN 需走代理才能下载）"""
    try:
        from nonebot import get_driver
        proxy = getattr(
            get_driver().config,
            "http_proxy",
            None
        )
        return proxy or None
    except Exception:
        return None


# =====================================================
# 媒体下载并发控制 + 超时 + 大小上限
#
# 突发转发时若不加限制，每条消息内的媒体下载会无限并发，
# 打爆 CDN 与内存；这里用全局信号量限制同时下载数。
# 下载上限默认 30MB：超过的文件 QQ 富媒体基本也传不上去，
# 直接放弃下载，由调用方降级为文字 + 原链接。
# 超时/并发数/大小上限均可通过 NoneBot 配置覆盖
# （.env.prod 的 API_TIMEOUT / MEDIA_CONCURRENCY / MEDIA_MAX_BYTES）。
# =====================================================

DEFAULT_MEDIA_TIMEOUT = 120
DEFAULT_MEDIA_CONCURRENCY = 4
DEFAULT_MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024

_semaphore = None
_semaphore_lock = threading.Lock()


def _get_semaphore():
    """懒初始化全局下载信号量（避免在事件循环创建前绑定 loop）"""
    global _semaphore
    if _semaphore is None:
        with _semaphore_lock:
            if _semaphore is None:
                concurrency = DEFAULT_MEDIA_CONCURRENCY
                try:
                    from nonebot import get_driver
                    concurrency = getattr(
                        get_driver().config,
                        "media_concurrency",
                        DEFAULT_MEDIA_CONCURRENCY
                    ) or DEFAULT_MEDIA_CONCURRENCY
                except Exception:
                    pass
                _semaphore = asyncio.Semaphore(concurrency)
    return _semaphore


def _get_timeout():
    """媒体下载超时（秒）：读 NoneBot 配置 api_timeout，默认 120"""
    try:
        from nonebot import get_driver
        timeout = getattr(
            get_driver().config,
            "api_timeout",
            DEFAULT_MEDIA_TIMEOUT
        )
        return timeout or DEFAULT_MEDIA_TIMEOUT
    except Exception:
        return DEFAULT_MEDIA_TIMEOUT


def _get_max_download_bytes():
    """媒体下载大小上限（字节）：读 NoneBot 配置 media_max_bytes，默认 30MB"""
    try:
        from nonebot import get_driver
        value = getattr(
            get_driver().config,
            "media_max_bytes",
            DEFAULT_MAX_DOWNLOAD_BYTES
        )
        return int(value) or DEFAULT_MAX_DOWNLOAD_BYTES
    except Exception:
        return DEFAULT_MAX_DOWNLOAD_BYTES


async def fetch_bytes(url):
    """下载媒体字节（走代理、受全局信号量限流、有大小上限）；
    失败或超限返回 None（调用方降级为链接）"""
    proxy = _get_proxy()
    timeout = _get_timeout()
    max_bytes = _get_max_download_bytes()
    try:
        async with _get_semaphore():
            async with httpx.AsyncClient(
                proxy=proxy,
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        logger.warning(
                            "媒体下载失败: {} {}",
                            resp.status_code,
                            url
                        )
                        return None

                    length = resp.headers.get("content-length")
                    if (
                        length
                        and length.isdigit()
                        and int(length) > max_bytes
                    ):
                        logger.warning(
                            "媒体超过大小上限({}>{})，放弃下载: {}",
                            length,
                            max_bytes,
                            url
                        )
                        return None

                    data = bytearray()
                    async for chunk in resp.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            logger.warning(
                                "媒体超过大小上限，放弃下载: {}",
                                url
                            )
                            return None
                    return bytes(data)
    except Exception as exc:
        logger.warning(
            "媒体下载异常: {}",
            exc
        )
        return None


async def fetch_bytes_many(urls):
    """并发下载多个 URL（受同一信号量限流）；返回 {url: bytes|None}

    解决消息内媒体逐个 await 串行下载的问题。"""
    if not urls:
        return {}
    results = await asyncio.gather(
        *(fetch_bytes(url) for url in urls)
    )
    return dict(zip(urls, results))
