from nonebot import get_app
from nonebot import get_bots

from nonebot.adapters.discord import Bot as DiscordBot
from nonebot.adapters.qq import Bot as QQBot


# =====================================================
# bot 健康接口（供管理面板显示真实运行状态）
#
# get_bots() 只包含已成功完成网关握手的 bot：
# 握手失败（如启动时网络异常）返回空；连上后断线会被移除。
# 面板 /api/status 询问本接口，据此把徽章区分成
# "运行中（已连接）/ 运行中（未连接）/ 已停止"。
# =====================================================

app = get_app()


@app.get("/api/health")
async def api_health():
    """返回 Discord/QQ 实时连接状态（供面板显示真实运行状态）"""
    bots = get_bots()
    return {
        "ok": True,
        "discord": any(
            isinstance(b, DiscordBot)
            for b in bots.values()
        ),
        "qq": any(
            isinstance(b, QQBot)
            for b in bots.values()
        ),
    }
