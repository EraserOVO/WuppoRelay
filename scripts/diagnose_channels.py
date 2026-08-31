# -*- coding: utf-8 -*-
"""频道权限诊断与记录脚本

列出 WuppoRelay Bot 当前可见（View Channel）与可读内容
（Read Message History）的 Discord 频道，并生成/更新
docs/CHANNELS.md 记录文件。

用法：
    python scripts/diagnose_channels.py

权限变化后重新运行即可更新记录。脚本不输出 token。
"""
import asyncio
import datetime
import json
import os
import sys

import httpx

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE, ".env.prod")
OUT_FILE = os.path.join(BASE, "docs", "CHANNELS.md")

# 文字频道 type（0=文字, 5=公告）
TEXT_TYPES = (0, 5)


def load_env(path):
    """解析 .env 文件（支持多行 JSON 值），返回 dict"""
    env = {}
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("#") or "=" not in line:
            i += 1
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        # 多行值：括号未闭合时继续拼接后续行
        if v.count("[") > v.count("]") or v.count("{") > v.count("}"):
            buf = [v]
            i += 1
            while i < len(lines):
                buf.append(lines[i])
                joined = "\n".join(buf)
                if (
                    joined.count("[") == joined.count("]")
                    and joined.count("{") == joined.count("}")
                ):
                    break
                i += 1
            v = "\n".join(buf)
        # 去掉外层配对引号
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k] = v
        i += 1
    return env


def load_token_proxy():
    """从 .env.prod 读取 Discord token 与代理（不输出 token 本身）"""
    if not os.path.exists(ENV_FILE):
        print(f"[错误] 未找到 {ENV_FILE}")
        return "", None
    env = load_env(ENV_FILE)
    try:
        bots = json.loads(env.get("DISCORD_BOTS", "[]"))
        token = bots[0]["token"] if bots else ""
    except Exception:
        token = ""
    proxy = env.get("HTTP_PROXY") or env.get("http_proxy") or None
    return token, proxy


async def main():
    token, proxy = load_token_proxy()
    if not token:
        print("[错误] 未读取到 DISCORD_BOTS token，请检查 .env.prod 配置")
        return 1

    headers = {"Authorization": f"Bot {token}"}
    # guild 名 → {guild_id, 频道列表 [(name, id, readable)]}
    result = []
    total_visible = 0
    total_readable = 0

    async with httpx.AsyncClient(proxy=proxy, timeout=20) as client:
        r = await client.get(
            "https://discord.com/api/v10/users/@me/guilds", headers=headers
        )
        if r.status_code != 200:
            print(f"[错误] 服务器列表请求失败: {r.status_code} {r.text}")
            return 1

        for g in r.json():
            gid = g["id"]
            gname = g.get("name", "?")
            r2 = await client.get(
                f"https://discord.com/api/v10/guilds/{gid}/channels", headers=headers
            )
            if r2.status_code != 200:
                print(f"[跳过] 服务器 [{gname}] 频道列表失败: {r2.status_code}")
                continue

            text_channels = [
                c for c in r2.json()
                if c.get("type") in TEXT_TYPES
            ]
            total_visible += len(text_channels)

            # 并发测试可读性（信号量限流，避免打爆 Discord 限流）
            sem = asyncio.Semaphore(5)

            async def test_channel(ch):
                async with sem:
                    r3 = await client.get(
                        f"https://discord.com/api/v10/channels/{ch['id']}/messages?limit=1",
                        headers=headers,
                    )
                    return ch, r3.status_code == 200

            tested = await asyncio.gather(
                *(test_channel(c) for c in text_channels)
            )

            channels = []
            for ch, readable in tested:
                if readable:
                    total_readable += 1
                channels.append((ch.get("name", "?"), ch["id"], readable))

            result.append((gname, gid, channels))

    # 写 docs/CHANNELS.md
    lines = []
    lines.append("# WuppoRelay Bot 频道权限记录")
    lines.append("")
    lines.append(
        f"> 由 scripts/diagnose_channels.py 生成，更新于 "
        f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    lines.append("> 权限变化后重新运行 `python scripts/diagnose_channels.py` 即可更新本文件。")
    lines.append("")
    lines.append("## 权限模型")
    lines.append("")
    lines.append("- **View Channel（查看频道）**：能列出并看到频道名（对应下表\"可见频道\"）")
    lines.append("- **Read Message History（读取消息历史）**：能通过 API 读取消息内容，")
    lines.append("  relay 链接转发与历史补发需要此权限（对应下表\"实际可读取内容\"）")
    lines.append("")
    lines.append(f"## 实际可读取内容的频道（{total_readable} 个）")
    lines.append("")
    lines.append("| 服务器 | 频道 | ID |")
    lines.append("|---|---|---|")
    for gname, gid, channels in result:
        for cname, cid, readable in channels:
            if readable:
                lines.append(f"| {gname} | #{cname} | {cid} |")
    lines.append("")
    lines.append(f"## 可见频道（{total_visible} 个，含不可读）")
    lines.append("")
    for gname, gid, channels in result:
        lines.append(f"### {gname}（{gid}）")
        lines.append("")
        lines.append("| 频道 | ID | 可读内容 |")
        lines.append("|---|---|---|")
        for cname, cid, readable in channels:
            lines.append(f"| #{cname} | {cid} | {'✅' if readable else '❌ 403'} |")
        lines.append("")

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # 写结构化快照 data/channels_audit.json（供面板读取可读频道列表）
    audit = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "visible_total": total_visible,
        "readable_total": total_readable,
        "readable": [
            {"guild": gname, "name": f"#{cname}", "id": cid}
            for gname, gid, channels in result
            for cname, cid, readable in channels
            if readable
        ],
        "visible": [
            {"guild": gname, "name": f"#{cname}", "id": cid, "readable": readable}
            for gname, gid, channels in result
            for cname, cid, readable in channels
        ],
    }

    audit_file = os.path.join(BASE, "data", "channels_audit.json")
    os.makedirs(os.path.dirname(audit_file), exist_ok=True)
    with open(audit_file, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)

    print(f"可见频道 {total_visible} 个，可读内容 {total_readable} 个")
    print(f"记录已写入 {OUT_FILE} 与 {audit_file}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
