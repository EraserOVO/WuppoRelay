from plugins.json_io import (
    load_json,
    atomic_write_json,
)


IDENTITIES_FILE = "data/qq_identities.json"

# 最后活动时间落盘节流：距上次已存值超过该秒数才更新，
# 避免每条消息都写盘（QQ 消息量不小）。
LAST_ACTIVE_THROTTLE = 300


# =====================================================
# QQ OpenID 身份资料（独立存储）
#
# 单一职责：记录 QQ 用户/群的身份信息（昵称/群名、最后活动
# 时间、管理员备注），供管理面板展示识别。与白名单/权限机制
# 完全独立 —— qq_user_openids 仍是唯一权限来源，这里只存身份
# 资料，绝不参与放行判断。
#
# 文件结构（data/qq_identities.json）：
#   {
#     "users":  { "<user_openid>":  {"nickname": "...", "last_active": 1756..., "admin_remark": ""} },
#     "groups": { "<group_openid>": {"group_name": "...", "last_active": 1756..., "admin_remark": ""} }
#   }
# 名称字段缺失/为空即视为「未命名」，由面板兜底显示，不在
# 存储里写死「未命名」字样。admin_remark 由管理面板手动维护。
# =====================================================


def load_identities():
    """读取全部身份资料 dict（缺失/损坏返回 {}）"""
    data = load_json(IDENTITIES_FILE, default={})
    if not isinstance(data, dict):
        return {}
    return data


def _persist(identities):
    atomic_write_json(IDENTITIES_FILE, identities, indent=4)


def _record(kind, openid, name_field, name, last_active):
    """写入/更新一条身份资料；仅在确实变化时落盘"""
    openid = str(openid or "").strip()
    if not openid:
        return
    data = load_identities()
    bucket = data.setdefault(kind, {})
    if not isinstance(bucket, dict):
        bucket = {}
        data[kind] = bucket
    entry = bucket.get(openid)
    is_new = not isinstance(entry, dict)
    if is_new:
        entry = {}
        bucket[openid] = entry

    changed = is_new
    if name:
        name = str(name).strip()
        if entry.get(name_field) != name:
            entry[name_field] = name
            changed = True
    if last_active:
        old = entry.get("last_active")
        if old is None or last_active - old >= LAST_ACTIVE_THROTTLE:
            entry["last_active"] = last_active
            changed = True

    if changed:
        _persist(data)


def record_user_identity(openid, nickname=None, last_active=None):
    """记录用户身份：openid + 昵称（可为空）+ 最后活动时间"""
    _record("users", openid, "nickname", nickname, last_active)


def record_group_identity(openid, group_name=None, last_active=None):
    """记录群身份：openid + 群名（可为空）+ 最后活动时间"""
    _record("groups", openid, "group_name", group_name, last_active)


def set_admin_remark(kind, openid, remark):
    """设置管理员备注；kind 为 'users' 或 'groups'。返回是否成功"""
    if kind not in ("users", "groups"):
        return False
    openid = str(openid or "").strip()
    if not openid:
        return False
    data = load_identities()
    bucket = data.setdefault(kind, {})
    if not isinstance(bucket, dict):
        bucket = {}
        data[kind] = bucket
    entry = bucket.get(openid)
    if not isinstance(entry, dict):
        entry = {}
        bucket[openid] = entry
    entry["admin_remark"] = str(remark or "")
    _persist(data)
    return True
