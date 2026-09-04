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
# 设置文件不存在时初始化默认值；文件损坏/结构无效时内存降级为
# 默认值并原样保留文件，绝不用默认值覆盖真实配置。
# =====================================================

from plugins.json_io import (
    load_json,
    atomic_write_json,
)

SETTINGS_FILE = "data/settings.json"

# 转发组：固定 id + 可重命名 name + channels[]/groups[] 集合。
# 旧配置缺省时内存归一化为默认转发组（channels/groups = 当前全局启用项），
# 路由结果与旧的"全局启用即全量转发"完全一致；面板保存后固化落盘。
FORWARDING_GROUP_DEFAULT_ID = "default"

# 「测试组」：固定 id + 固定名称，不可删除、不可改名，计入转发组数量上限。
# 测试隔离完全由它实现：加入测试组的频道/QQ群即属于测试路由，
# 不再使用频道/QQ群自身的 is_test 属性（旧测试属性由迁移并入测试组）。
TEST_FORWARDING_GROUP_ID = "test"
TEST_FORWARDING_GROUP_NAME = "测试组"


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
    """settings.json 完全不存在时用默认值初始化（首跑引导）。

    文件存在但损坏/结构无效时绝不写盘：用默认值覆盖会清空真实
    配置（群/频道/白名单）。此处只保留原文件，由 _load_settings
    记错误日志并内存降级，管理员修复文件后 mtime 变化自动恢复。"""
    if os.path.exists(SETTINGS_FILE):
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
        # 文件损坏/结构无效：绝不用默认值覆盖原文件（会清空真实配置），
        # 记错误日志并内存降级为安全默认值，原文件原样保留；
        # 修复后 mtime 变化会自动重新加载
        # 延迟导入：面板进程也会导入本模块，避免模块级引入 nonebot
        from nonebot import logger
        logger.error(
            "settings.json 损坏或结构无效，已临时改用内存默认配置"
            "（原文件未改动，修复后自动恢复）: {}",
            SETTINGS_FILE,
        )
        data = None
    elif not isinstance(data.get("qq_user_openids"), list):
        # 结构完整但缺少新增字段：只在内存补齐，不写回文件
        # （写回会与面板保存形成跨进程写竞争）
        data["qq_user_openids"] = []

    if data is None:
        _settings_cache = _default_settings()
    else:
        _settings_cache = data
    # 转发组内存归一化（不写盘）：旧配置缺省/损坏时生成默认组，
    # 保证路由函数始终拿到合法结构；与下方 qq_user_openids 的内存
    # 补齐同理，绝不把归一化结果当作面板配置写回文件
    _ensure_forwarding_groups(_settings_cache)
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


# =====================================================
# 转发组路由
#
# forwarding_groups 只负责"当前路由"，不管理去重游标：
# 从转发组取消某个群只影响该频道此后转发到哪些群，
# 不触碰 data/discord_last.json 里该频道×群已有的历史游标
# （游标结构/生命周期由 plugins/dedup.py + plugins/history.py
# 管理，本模块不参与）。
#
# 实际发送必须同时满足（两级开关）：
#   - 频道全局 enabled（discord_channels 条目的 enabled）
#   - 群全局 enabled（qq_group_openids 条目的 enabled）
#   - 频道与群同属于至少一个转发组
# =====================================================


def _enabled_channel_ids(data):
    """settings dict 内全局启用的频道 id 列表（转发组归一化/路由共用）"""
    items = data.get("discord_channels")
    if isinstance(items, list):
        return [
            str(item["id"])
            for item in items
            if item.get("enabled") and item.get("id")
        ]
    return [str(k) for k in DISCORD_CHANNELS]


def _enabled_group_openids(data):
    """settings dict 内全局启用的群 openid 列表（转发组归一化/路由共用）"""
    items = data.get("qq_group_openids")
    if isinstance(items, list):
        return [
            str(item["openid"])
            for item in items
            if item.get("enabled") and item.get("openid")
        ]
    return [str(o) for o in QQ_GROUP_OPENIDS]


def _ensure_forwarding_groups(data):
    """转发组内存归一化（只改内存 data，不写盘）。

    - 结构合法 → 清洗保留（id 去重、字段转 str、name 缺省回退 id）
    - 缺省/空/结构无效（旧配置升级）→ 生成默认转发组 + 「测试组」：
      默认组 = 当前全部启用且非测试的频道/群；测试组 = 旧 is_test 标记的频道/群，
      与旧的"全局启用即全量转发（测试/非测试分开路由）"行为一致。
    - 始终确保「测试组」存在：id 固定、名称固定，不可删除/改名。
    - 旧 is_test 属性废弃：读取后从条目中移除，成员并入「测试组」（幂等）。"""
    groups = data.get("forwarding_groups")
    if not isinstance(groups, list):
        groups = []

    # 旧测试属性 → 测试组成员（先收集，再清洗/生成，最后移除 is_test 字段）
    legacy_test_groups = {
        str(item["openid"])
        for item in data.get("qq_group_openids", [])
        if isinstance(item, dict) and item.get("is_test") and item.get("openid")
    }
    legacy_test_channels = {
        str(item["id"])
        for item in data.get("discord_channels", [])
        if isinstance(item, dict) and item.get("is_test") and item.get("id")
    }
    for item in data.get("qq_group_openids", []):
        if isinstance(item, dict):
            item.pop("is_test", None)
    for item in data.get("discord_channels", []):
        if isinstance(item, dict):
            item.pop("is_test", None)

    normalized = []
    seen_ids = set()
    for item in groups:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("id") or "").strip()
        if not gid or gid in seen_ids:
            continue
        seen_ids.add(gid)
        normalized.append({
            "id": gid,
            "name": str(item.get("name") or "").strip() or gid,
            "channels": [str(c) for c in (item.get("channels") or []) if c],
            "groups": [str(o) for o in (item.get("groups") or []) if o],
        })

    if not normalized:
        normalized = [{
            "id": FORWARDING_GROUP_DEFAULT_ID,
            "name": "转发组1",
            "channels": [
                c for c in _enabled_channel_ids(data)
                if c not in legacy_test_channels
            ],
            "groups": [
                o for o in _enabled_group_openids(data)
                if o not in legacy_test_groups
            ],
        }]

    # 确保「测试组」存在：id/name 固定；旧 is_test 成员并入（去重，幂等）
    test_fg = next(
        (fg for fg in normalized if fg["id"] == TEST_FORWARDING_GROUP_ID),
        None,
    )
    if test_fg is None:
        test_fg = {
            "id": TEST_FORWARDING_GROUP_ID,
            "name": TEST_FORWARDING_GROUP_NAME,
            "channels": sorted(legacy_test_channels),
            "groups": sorted(legacy_test_groups),
        }
        normalized.append(test_fg)
    else:
        test_fg["name"] = TEST_FORWARDING_GROUP_NAME  # 测试组不可改名
        test_fg["channels"] = sorted(set(test_fg["channels"]) | legacy_test_channels)
        test_fg["groups"] = sorted(set(test_fg["groups"]) | legacy_test_groups)

    data["forwarding_groups"] = normalized
    return normalized


def get_forwarding_groups():
    """返回归一化后的转发组列表（[{id, name, channels[], groups[]}]）"""
    return _ensure_forwarding_groups(
        _load_settings()
    )


def get_groups_for_channel(channel_id):
    """Discord 频道 → QQ 目标群 的唯一路由入口（两级开关）。

    同时满足才转发：
      - 频道全局 enabled
      - 群全局 enabled
      - 频道与群同属于至少一个转发组
    不在任何转发组的频道不转发（全局启用但未勾选任何组 = 该频道暂停）。
    返回值是去重后的 openid 列表，顺序与全局启用群一致；
    同一群被多个转发组勾选不会重复出现（下游 inflight 判重兜底）。"""
    data = _load_settings()
    channel_id = str(channel_id)

    enabled_channels = set(
        _enabled_channel_ids(data)
    )

    if channel_id not in enabled_channels:
        return []

    enabled_groups = _enabled_group_openids(data)

    if not enabled_groups:
        return []

    targets = set()
    for fg in _ensure_forwarding_groups(data):
        if channel_id not in fg["channels"]:
            continue
        targets.update(fg["groups"])

    # 群必须全局 enabled；按启用顺序返回即天然去重
    return [
        group
        for group in enabled_groups
        if group in targets
    ]


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


def get_test_group_openids():
    """返回「测试组」内的 QQ 群 openid 集合。

    测试隔离完全由「测试组」实现：加入测试组的群即属于测试路由，
    不再读取群条目的 is_test 属性。供 QQ→QQ 手动转发（qq_fwd）
    判断目标群归属使用；缺省时返回空集合。"""
    data = _load_settings()
    for fg in _ensure_forwarding_groups(data):
        if fg["id"] == TEST_FORWARDING_GROUP_ID:
            return set(fg["groups"])
    return set()


# =====================================================
# QQ→QQ 手动转发配置
#
# 不依赖转发组（forwarding_groups 只表达 Discord频道→QQ群 路由），
# QQ→QQ 转发目标候选由「已注册群条目」的 enabled 与「测试组」成员关系决定。
# =====================================================

def get_qq_group_entries():
    """返回 settings 中全部已注册 QQ 群条目（原样 dict 列表）。

    仅用于枚举候选目标群（openid/name/enabled），不参与
    Discord→QQ 权限判断；缺失/损坏时回退空列表。"""
    data = _load_settings()
    items = data.get("qq_group_openids")
    if isinstance(items, list):
        return [
            dict(item)
            for item in items
            if isinstance(item, dict) and item.get("openid")
        ]
    return []


def get_qq_fwd_recency_limit():
    """“上一条消息”可接受的最大时间差（秒），默认 1800（30 分钟）"""
    data = _load_settings()
    try:
        value = int(data.get("qq_fwd_recency_limit") or 1800)
    except (TypeError, ValueError):
        return 1800
    return value if value > 0 else 1800


def get_qq_fwd_select_timeout():
    """交互选择超时（秒），默认 60；超时后该用户的待转发请求作废"""
    data = _load_settings()
    try:
        value = int(data.get("qq_fwd_select_timeout") or 60)
    except (TypeError, ValueError):
        return 60
    return value if value > 0 else 60


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


def get_channel_filter(channel_id):
    """返回指定频道的消息筛选配置

    {"filter_usernames": [...], "filter_keywords": [...]}
    旧配置没有筛选字段时返回空列表（= 不筛选）。
    兼容旧字段 filter_user_ids：如果存在则转换为 filter_usernames。
    """
    data = _load_settings()
    items = data.get("discord_channels")
    if isinstance(items, list):
        for item in items:
            if str(item.get("id")) == str(channel_id):
                # 优先读新字段 filter_usernames，兼容旧字段 filter_user_ids
                usernames = item.get("filter_usernames")
                if usernames is None:
                    usernames = item.get("filter_user_ids") or []
                return {
                    "filter_usernames": [
                        str(u).strip()
                        for u in usernames
                        if str(u).strip()
                    ],
                    "filter_keywords": [
                        str(k)
                        for k in (item.get("filter_keywords") or [])
                        if k
                    ],
                }
    return {"filter_usernames": [], "filter_keywords": []}
