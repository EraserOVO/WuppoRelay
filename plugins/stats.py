import time

from plugins.json_io import (
    load_json,
    atomic_write_json,
)


STATS_FILE = "data/stats.json"


# =====================================================
# 转发统计（B4）
#
# 单一职责：累计转发成功/失败条数 + 当日条数。
# 只在 relay.py 内自增，进程内单点写，故用内存缓存；
# 管理面板直接读 data/stats.json 展示。
# =====================================================

_stats = None


def _load():

    global _stats

    if _stats is None:

        data = load_json(
            STATS_FILE,
            default={}
        )

        _stats = data if isinstance(data, dict) else {}

    return _stats


def get_stats():
    """返回统计 dict（面板直接读文件也行，这里供插件内使用）"""
    return dict(_load())


def record(ok):
    """转发一条消息后记录统计（ok = 至少一个群送达）"""
    data = _load()

    today = time.strftime("%Y-%m-%d")

    if data.get("today") != today:
        data["today"] = today
        data["today_forwarded"] = 0
        data["today_failed"] = 0

    if ok:
        data["total_forwarded"] = int(data.get("total_forwarded", 0)) + 1
        data["today_forwarded"] = int(data.get("today_forwarded", 0)) + 1
    else:
        data["total_failed"] = int(data.get("total_failed", 0)) + 1
        data["today_failed"] = int(data.get("today_failed", 0)) + 1

    atomic_write_json(
        STATS_FILE,
        data,
        indent=2
    )
