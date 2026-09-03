# =====================================================
# Discord 消息去重规则（纯函数）
#
# 单一职责：回答"该不该发 / 该发给谁 / 发成功后怎么记"。
# 不碰文件 I/O、不碰 fetch、不碰 send、不碰日志。
# 所有持久化由调用方通过 history.py 的 load_last_messages /
# save_last_messages 完成。
#
# 数据格式与 data/discord_last.json 完全兼容：
#   {
#     "频道ID": {
#       "群openid": "最后成功转发的消息ID",
#       ...
#       "*": "旧格式兜底记录"
#     },
#     ...
#   }
# =====================================================


def normalize_channel_map(
    last_messages: dict,
    channel_id: str,
) -> dict:
    """取出 channel_id 对应的 channel_map，并保证是 dict 格式。

    旧格式 {channel: id_str} 会被迁移为 {channel: {"*": id_str}}，
    迁移结果原地写回 last_messages[channel_id]，调用方 save 时一并落盘。
    值为空 / 非 dict 时返回空 dict（不写回空字符串）。"""

    channel_map = last_messages.get(channel_id)

    if not isinstance(channel_map, dict):
        channel_map = {"*": channel_map} if channel_map else {}
        last_messages[channel_id] = channel_map

    return channel_map


def select_target_groups(
    channel_map: dict,
    active_groups: list[str],
    message_id: str,
) -> list[str]:
    """返回需要发送的群列表。

    规则：
    - group 专属 last_id 优先，缺失回退 "*" 兜底
    - last_id 为空（该群无记录）→ 发送
    - int(message_id) > int(last_id) → 发送
    - 否则跳过
    """

    targets = []

    for group in active_groups:
        last_id = channel_map.get(group) or channel_map.get("*")
        if last_id is None or int(message_id) > int(last_id):
            targets.append(group)

    return targets


def compute_base_id(
    channel_map: dict,
    active_groups: list[str],
) -> str | None:
    """各群有效 last_id 的最小值（供补发确定拉取起点）。

    规则：
    - 各群 last_id = channel_map[group] 优先，缺失回退 "*"
    - 空值不纳入
    - 全无有效记录返回 None（首次启用场景）
    """

    effective_ids = []

    for group in active_groups:
        last_id = channel_map.get(group) or channel_map.get("*")
        if last_id:
            effective_ids.append(last_id)

    if not effective_ids:
        return None

    return min(effective_ids, key=int)


def apply_baseline(
    channel_map: dict,
    active_groups: list[str],
    latest_id: str,
    include_star: bool = False,
) -> bool:
    """把 latest_id 写到每个活跃群作为基线（首次启用 / 清除待补发场景）。

    原地修改 channel_map，返回是否产生任一变更。
    include_star=True 时同时把 "*" 兜底键推进到 latest_id
    （清除待补发场景使用，避免后续旧格式兜底干扰）。"""

    changed = False

    for group in active_groups:
        if channel_map.get(group) != latest_id:
            channel_map[group] = latest_id
            changed = True

    if include_star and channel_map.get("*") != latest_id:
        channel_map["*"] = latest_id
        changed = True

    return changed


def apply_success(
    channel_map: dict,
    ok_map: dict[str, bool],
    message_id: str,
) -> bool:
    """只把 ok=True 的群 last_id 推进到 message_id，且只进不退：
    群已有更高游标（如延迟重试期间收到了更新的消息）时不回退，
    避免把补发 base 拉低造成已送达消息被重复发送。

    返回是否产生任一变更：
    - ok_map 为空（QQ 未连接等场景）→ 返回 False，调用方不写盘
    - ok=False 的群保持旧记录，由延迟重试/补发负责补送
    """

    changed = False

    for group, ok in ok_map.items():
        if not ok:
            continue
        current = channel_map.get(group) or channel_map.get("*")
        if (
            current is None
            or int(message_id) > int(current)
        ):
            channel_map[group] = message_id
            changed = True

    return changed
