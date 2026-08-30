import os

from plugins.json_io import (
    load_json,
    atomic_write_json,
)


LAST_MESSAGE_FILE = "data/discord_last.json"


# =====================================================
# Discord 消息 ID 持久化
#
# 单一职责：读写 data/discord_last.json，供转发去重使用。
# 高频转发时每条消息都读盘没有必要，这里做内存缓存，
# 通过文件 mtime + 大小校验感知外部修改（本文件只有本进程写，
# 缓存与磁盘始终一致；面板等进程不碰这个文件）。
# =====================================================

_last = None
_last_mtime = None
_last_size = None


def load_last_messages():

    global _last, _last_mtime, _last_size

    try:
        st = os.stat(LAST_MESSAGE_FILE)
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        mtime = size = None

    if (
        _last is not None
        and mtime == _last_mtime
        and size == _last_size
    ):
        return _last

    data = load_json(
        LAST_MESSAGE_FILE,
        default={}
    )

    _last = data if isinstance(data, dict) else {}
    _last_mtime, _last_size = mtime, size

    return _last


def save_last_messages(data):

    atomic_write_json(
        LAST_MESSAGE_FILE,
        data,
        indent=4
    )

    global _last, _last_mtime, _last_size

    _last = data

    try:
        st = os.stat(LAST_MESSAGE_FILE)
        _last_mtime, _last_size = st.st_mtime, st.st_size
    except OSError:
        pass
