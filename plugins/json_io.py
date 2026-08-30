import json
import os


# =====================================================
# JSON 文件读写
#
# 单一职责：统一的 JSON 持久化工具。
# 写采用 tmp + os.replace 原子替换，避免进程崩溃时
# 写坏目标文件（与面板 settings.json 的写法一致）。
# 供 command.py / history.py / config.py 复用。
# =====================================================


def load_json(path, default=None):
    """读取 JSON；文件缺失或损坏时返回 default"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def atomic_write_json(path, data, indent=4):
    """原子写 JSON：先写临时文件，再 os.replace 替换目标"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)
