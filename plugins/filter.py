# =====================================================
# Discord 频道消息筛选（纯函数）
#
# 单一职责：判断一条消息是否应该被转发。
# 不碰 I/O、不碰 fetch、不碰 send、不碰日志。
# 由 relay.py 和 backfill.py 共用，保证筛选逻辑唯一。
#
# 筛选规则：
# - user_ids 为空 → 不限制用户
# - keywords 为空 → 不限制关键词
# - 两者同时存在 → AND（必须同时满足）
# - 多个 user_id 之间 → OR
# - 多个 keywords 之间 → OR
# - 关键词使用普通文本包含匹配，不区分大小写，不使用正则
# =====================================================


def check_message_filter(
    filter_config: dict,
    author_username: str,
    content: str,
    embeds: list = None,
) -> bool:
    """判断消息是否通过筛选，返回 True 表示应该转发。

    filter_config: {"filter_usernames": [...], "filter_keywords": [...]}
    author_username: 消息作者的 Discord username（字符串）
    content: 消息原始文本内容
    embeds: 消息嵌入列表 [{"title": "..."}, ...]，可选
    """
    usernames = filter_config.get("filter_usernames") or []
    keywords = filter_config.get("filter_keywords") or []

    # 无任何筛选条件 → 全部转发
    if not usernames and not keywords:
        return True

    # 用户筛选（OR，不区分大小写）
    if usernames:
        author_lower = (author_username or "").lower()
        if not any(
            str(u).strip().lower() == author_lower
            for u in usernames
            if str(u).strip()
        ):
            return False

    # 关键词筛选（OR，不区分大小写）
    if keywords:
        parts = [content or ""]
        for embed in (embeds or []):
            title = None
            if isinstance(embed, dict):
                title = embed.get("title")
            else:
                title = getattr(embed, "title", None)
            if title:
                parts.append(str(title))
        search_text = " ".join(parts).lower()
        if not any(kw.lower() in search_text for kw in keywords if kw):
            return False

    return True
