import os

# Discord频道列表（可分发版本：不内置默认频道；
# 首次运行时自动生成空配置，由用户在管理面板添加）

DISCORD_CHANNELS = {}


# QQ 接收群（默认空；实际启用的群以 data/settings.json 为准）
#
# 官方机器人使用 group_openid，不是群号。
# group_openid 无法从群号推算，需要机器人入群后获取：
# 1. 启动 bot，把机器人拉进目标群（或在群里发一条消息）
# 2. 查看控制台输出的 "[QQ group_openid] ..."，
#    或读取 data/qq_group_openids.json
# 3. 在管理面板中启用该 openid
#
QQ_GROUP_OPENIDS = []


# =====================================================
# Discord Bot Token
#
# 不在此硬编码，唯一来源是 .env.prod 的 DISCORD_BOTS，
# 运行时通过 get_discord_token() 从 NoneBot 配置读取。
# =====================================================

def get_discord_token():
    """读取 .env.prod 中 DISCORD_BOTS 的 Discord Bot Token"""
    try:
        from nonebot import get_driver
        bots = getattr(
            get_driver().config,
            "discord_bots",
            None
        )
        if bots:
            first = bots[0]
            token = getattr(
                first,
                "token",
                None
            )
            if not token and isinstance(first, dict):
                token = first.get("token")
            if token:
                return token
    except Exception:
        pass
    return ""


# =====================================================
# 运行时配置
#
# 管理面板写入 data/settings.json，实时控制：
#   - 哪些 QQ 群接收转发（qq_group_openids）
#   - 哪些 Discord 频道参与转发（discord_channels）
# 设置文件不存在或结构不完整时，回退到上面的默认值。
# =====================================================

from plugins.json_io import (
    load_json,
    atomic_write_json,
)

SETTINGS_FILE = "data/settings.json"


def _default_settings():
    groups = [
        {"openid": str(o), "enabled": True, "remark": ""}
        for o in QQ_GROUP_OPENIDS
    ]
    channels = [
        {"id": str(k), "name": v, "enabled": True}
        for k, v in DISCORD_CHANNELS.items()
    ]
    return {
        "qq_group_openids": groups,
        "qq_user_openids": [],
        "discord_channels": channels,
        "backfill_enabled": True,
        "backfill_limit": 10,
    }


def _ensure_settings_file():
    """settings.json 缺失/损坏/结构不完整时，用默认值自动生成；
    结构完整但缺少新增字段时只补齐该字段，不重置已有配置，
    保证运行时配置始终落在 settings.json，避免与 config.py 默认值各说各话。"""
    data = load_json(
        SETTINGS_FILE,
        default=None
    )
    if (
        isinstance(data, dict)
        and isinstance(data.get("qq_group_openids"), list)
        and isinstance(data.get("discord_channels"), list)
    ):
        # 结构完整：仅补齐缺失的新增字段（qq_user_openids）
        if not isinstance(data.get("qq_user_openids"), list):
            data["qq_user_openids"] = []
            atomic_write_json(
                SETTINGS_FILE,
                data,
                indent=2
            )
        return

    atomic_write_json(
        SETTINGS_FILE,
        _default_settings(),
        indent=2
    )


# settings.json 高频读取（每条消息、每个群都会调用），
# 用内存缓存 + 文件 mtime/size 校验，避免每次重新解析 JSON；
# 面板改配置走原子替换，mtime 会变化，缓存最长滞后一次读取。
_settings_cache = None
_settings_cache_mtime = None
_settings_cache_size = None


def _load_settings():

    global _settings_cache, _settings_cache_mtime, _settings_cache_size

    if not os.path.exists(SETTINGS_FILE):
        _ensure_settings_file()

    try:
        st = os.stat(SETTINGS_FILE)
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        mtime = size = None

    if (
        _settings_cache is not None
        and mtime == _settings_cache_mtime
        and size == _settings_cache_size
    ):
        return _settings_cache

    data = load_json(
        SETTINGS_FILE,
        default=None
    )

    if not (
        isinstance(data, dict)
        and isinstance(data.get("qq_group_openids"), list)
        and isinstance(data.get("discord_channels"), list)
    ):
        # 缺失/损坏/结构不完整：用默认值重建
        _ensure_settings_file()
        data = load_json(
            SETTINGS_FILE,
            default=None
        )
    elif not isinstance(data.get("qq_user_openids"), list):
        # 结构完整但缺少新增字段：只补齐，不重置已有配置
        data["qq_user_openids"] = []
        atomic_write_json(
            SETTINGS_FILE,
            data,
            indent=2
        )

    _settings_cache = data if isinstance(data, dict) else _default_settings()
    _settings_cache_mtime, _settings_cache_size = mtime, size

    return _settings_cache


def get_active_channels():
    """返回当前启用的 Discord 频道 {频道ID: 名称}

    设置文件中存在频道列表时，按勾选状态返回（全部未勾选 = 空 dict，
    即暂停转发，不再回退默认值）；仅当列表缺失或损坏时才回退默认值。"""
    data = _load_settings()
    items = data.get("discord_channels")
    if isinstance(items, list):
        active = {}
        for item in items:
            if item.get("enabled") and item.get("id"):
                active[str(item["id"])] = str(item.get("name") or "")
        return active
    return dict(DISCORD_CHANNELS)


def get_active_groups():
    """返回当前启用的 QQ 群 openid 列表

    设置文件中存在群列表时，按勾选状态返回（全部未勾选 = 空 list，
    即暂停转发，不再回退默认值）；仅当列表缺失或损坏时才回退默认值。"""
    data = _load_settings()
    items = data.get("qq_group_openids")
    if isinstance(items, list):
        active = []
        for item in items:
            if item.get("enabled") and item.get("openid"):
                active.append(str(item["openid"]))
        return active
    return list(QQ_GROUP_OPENIDS)


def get_all_channels():
    """返回设置文件中全部 Discord 频道 {频道ID: 名称}（不论是否启用）

    供私聊 relay 链接模式补频道显示名：链接指向的频道可能未启用，
    也要能显示面板配置的名字。缺失/损坏时回退默认值（空 dict）。"""
    data = _load_settings()
    items = data.get("discord_channels")
    if isinstance(items, list):
        all_channels = {}
        for item in items:
            if item.get("id"):
                all_channels[str(item["id"])] = str(item.get("name") or "")
        return all_channels
    return dict(DISCORD_CHANNELS)


def get_active_user_openids():
    """返回当前启用（允许私聊 relay）的 QQ 用户 openid 列表

    设置文件中存在用户列表时，按勾选状态返回；缺失/损坏时返回空列表
    （默认所有人不可用，需在管理面板勾选放行）。"""
    data = _load_settings()
    items = data.get("qq_user_openids")
    if isinstance(items, list):
        active = []
        for item in items:
            if item.get("enabled") and item.get("openid"):
                active.append(str(item["openid"]))
        return active
    return []


def get_backfill_limit():
    """每次启动补发时，每个频道最多补发的消息条数（默认 10）"""
    data = _load_settings()
    try:
        value = int(data.get("backfill_limit") or 10)
    except (TypeError, ValueError):
        return 10
    return value if value > 0 else 10


def get_backfill_enabled():
    """补发功能总开关（默认开启）"""
    data = _load_settings()
    value = data.get("backfill_enabled")
    if value is None:
        return True
    return bool(value)
