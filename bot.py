import asyncio
import json
import os

import nonebot

from nonebot import get_driver
from nonebot import logger

from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.adapters.discord import Adapter as DiscordAdapter


nonebot.init()


# =====================================================
# 日志轮转 + 运行时日志级别（B4）
#
# 全部日志写入 data/logs/bot.log，按 5MB 轮转、保留最近 3 个，
# 避免日志无限增长（此前 LOG_LEVEL=DEBUG 下一天能到 19MB）。
# 初始级别取 data/runtime_log_level.json（管理面板可改），
# 缺失时回退 .env.prod 的 LOG_LEVEL（当前 INFO，排查时可临时改 DEBUG）。
# 面板写入 runtime_log_level.json 后，后台任务 3 秒内自动生效，无需重启。
# =====================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BOT_LOG = os.path.join(
    BASE_DIR,
    "data",
    "logs",
    "bot.log",
)

RUNTIME_LEVEL_FILE = os.path.join(
    BASE_DIR,
    "data",
    "runtime_log_level.json",
)

os.makedirs(
    os.path.dirname(BOT_LOG),
    exist_ok=True
)

VALID_LOG_LEVELS = ("TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL")


def _file_log_level():
    """读取面板写入的运行时日志级别文件；无效/缺失返回 None"""
    try:
        with open(RUNTIME_LEVEL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        level = str(data.get("level", "")).upper()
        if level in VALID_LOG_LEVELS:
            return level
    except Exception:
        pass
    return None


def _resolve_log_level():
    """初始日志级别：优先运行时文件，其次 .env.prod 的 LOG_LEVEL"""
    level = _file_log_level()
    if level:
        return level
    try:
        level = str(getattr(get_driver().config, "log_level", "INFO")).upper()
        if level in VALID_LOG_LEVELS:
            return level
    except Exception:
        pass
    return "INFO"


_handler_id = None


def _setup_log_sink(level):
    """重建文件 sink（运行时切换日志级别用）"""
    global _handler_id
    if _handler_id is not None:
        logger.remove(_handler_id)
    _handler_id = logger.add(
        BOT_LOG,
        level=level,
        rotation="5 MB",
        retention=3,
        encoding="utf-8",
        enqueue=True,
    )
    logger.info(
        "日志级别 -> {}",
        level
    )


_setup_log_sink(
    _resolve_log_level()
)


driver = get_driver()


@driver.on_startup
async def _watch_log_level():
    """监听 runtime_log_level.json 的 mtime 变化，动态切换日志级别。
    注意：只能在这里创建后台任务，不能直接 await 监听循环——
    否则 startup 事件永不完成，后续适配器连接（Discord/QQ）不会执行。"""
    asyncio.create_task(_watch_log_level_loop())


async def _watch_log_level_loop():
    last_mtime = None
    while True:
        await asyncio.sleep(3)
        try:
            mtime = os.path.getmtime(RUNTIME_LEVEL_FILE)
        except OSError:
            mtime = None
        if mtime == last_mtime:
            continue
        last_mtime = mtime
        try:
            _setup_log_sink(
                _resolve_log_level()
            )
        except Exception:
            logger.exception("切换日志级别失败")


driver.register_adapter(
    QQAdapter
)


driver.register_adapter(
    DiscordAdapter
)


nonebot.load_plugins(
    "plugins"
)


nonebot.run()
