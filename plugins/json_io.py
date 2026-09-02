import json
import os
import time
import uuid


# =====================================================
# JSON 文件读写
#
# 单一职责：统一的 JSON 持久化工具。
# 写采用唯一 tmp + os.replace 原子替换，避免进程崩溃时
# 写坏目标文件（与面板 settings.json 的写法一致）。
# 供 command.py / history.py / config.py / 管理面板 复用。
# =====================================================


def load_json(path, default=None):
    """读取 JSON；文件缺失或损坏时返回 default"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# Windows 下目标文件正被其它进程打开读取时，os.replace 会报
# PermissionError（Python 的读句柄不带 FILE_SHARE_DELETE），
# 读取通常毫秒级结束，短间隔重试即可通过
REPLACE_ATTEMPTS = 3
REPLACE_RETRY_DELAY = 0.05


def atomic_write_json(path, data, indent=4):
    """原子写 JSON：先写唯一临时文件，再 os.replace 原子替换目标

    - 临时文件名带进程号 + 随机串：settings.json / qq_registrations.json /
      openid 发现记录等文件存在 bot 与面板两个写方，固定 tmp 名会让
      并发写共用同一临时文件而互相截断交错，最终把坏内容换进目标文件
    - os.replace 失败（目标被读取句柄短暂占用）时短间隔重试；
      重试耗尽则删除临时文件并抛出，目标文件保持旧内容"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = "%s.%s.%s.tmp" % (path, os.getpid(), uuid.uuid4().hex)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(REPLACE_RETRY_DELAY)
