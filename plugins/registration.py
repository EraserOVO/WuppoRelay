import time

from nonebot import logger
from nonebot import on_message
from nonebot.adapters import Bot, Event

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    GroupMessageCreateEvent,
)

from plugins.config import get_active_user_openids
from plugins.json_io import (
    load_json,
    atomic_write_json,
)


# =====================================================
# QQ 用户/群 注册审核
#
# 自动发现的 openid 只记录（data/qq_group_openids.json /
# data/qq_user_openids.json），不进入面板同步待选名单；只有
# 主动注册后（本模块）才进入面板同步弹窗的待审核名单。
#
# 注册数据独立存储（data/qq_registrations.json），不混入白名单：
#   {
#     "users":  {"<user_openid>":  {"qq_id": "...", "nickname": "...", "time": ...}},
#     "groups": {"<group_openid>": {"qq_id": "...", "group_name": "...",
#                                   "operator_openid": "...", "time": ...}}
#   }
# 以 openid 为键，同一 openid 同时只有一条注册记录（重新注册覆盖旧申请；
# 被拒绝后重新注册会再次出现在审核列表）。
#
# 注册 ≠ 授权：qq_user_openids / qq_group_openids 仍是唯一权限来源。
# 审核通过只是按现有同步流程把 openid 加入 settings（默认禁用），
# 是否放行仍由管理员在面板勾选。
# =====================================================

REGISTRATIONS_FILE = "data/qq_registrations.json"


def load_registrations():
    """读取注册申请 dict（缺失/损坏返回空结构）"""
    data = load_json(REGISTRATIONS_FILE, default={})
    if not isinstance(data, dict):
        data = {}
    for kind in ("users", "groups"):
        if not isinstance(data.get(kind), dict):
            data[kind] = {}
    return data


def register_user(user_openid, qq_id, nickname):
    """提交/更新用户注册申请，绑定当前 User OpenID"""
    openid = str(user_openid or "").strip()
    if not openid:
        return False
    data = load_registrations()
    data["users"][openid] = {
        "qq_id": str(qq_id).strip(),
        "nickname": str(nickname).strip(),
        "time": int(time.time()),
    }
    atomic_write_json(REGISTRATIONS_FILE, data, indent=4)
    return True


def register_group(group_openid, qq_id, group_name, operator_openid):
    """提交/更新群注册申请，绑定当前 Group OpenID 与操作人 openid"""
    openid = str(group_openid or "").strip()
    if not openid:
        return False
    data = load_registrations()
    data["groups"][openid] = {
        "qq_id": str(qq_id).strip(),
        "group_name": str(group_name).strip(),
        "operator_openid": str(operator_openid or "").strip(),
        "time": int(time.time()),
    }
    atomic_write_json(REGISTRATIONS_FILE, data, indent=4)
    return True


def remove_registration(kind, openid):
    """删除注册申请（审核通过/拒绝后由面板调用）；返回是否存在"""
    if kind not in ("users", "groups"):
        return False
    openid = str(openid or "").strip()
    if not openid:
        return False
    data = load_registrations()
    existed = data.get(kind, {}).pop(openid, None) is not None
    if existed:
        atomic_write_json(REGISTRATIONS_FILE, data, indent=4)
    return existed


# =====================================================
# QQ 注册指令
#
# 用户注册（私聊）：register <QQ号> <昵称> —— 任何用户可用
#   （注册 ≠ 授权，审核通过后仍需管理员在面板勾选放行）
# 群注册（群聊）：register-group <群号> <群名称> —— 仅白名单用户可用
#   （qq_user_openids 是唯一权限来源；QQ 群消息事件中发送者
#   标识为 member_openid，按其校验白名单）
#
# 中文"注册"已删除：中文消息不触发注册，由 manage.py 按原有
# 未知命令/未授权路径回复，避免中文聊天误触发。
# relay 前缀消息由 plugins/command.py 处理；register 前缀消息
# 由本模块处理（manage.py 已跳过，避免双重回复）。
# =====================================================

USER_REGISTER_USAGE = "用法：register QQ号 昵称（例：register 123456789 张三）"
GROUP_REGISTER_USAGE = "用法：register-group 群号 群名称（例：register-group 987654321 测试群）"

register_command = on_message(
    priority=15,
    block=False
)


@register_command.handle()
async def handle_register(
    bot: Bot,
    event: Event
):

    if not isinstance(
        bot,
        QQBot
    ):
        return

    message = event.get_plaintext().strip()

    if message == "register" or message.startswith("register "):

        if not isinstance(event, C2CMessageCreateEvent):
            # 用户注册只接受私聊提交
            await bot.send(event, "用户注册请私聊机器人发送：" + USER_REGISTER_USAGE)
            return

        await _handle_user_register(bot, event, message)

    elif message == "register-group" or message.startswith("register-group "):

        if not isinstance(event, GroupMessageCreateEvent):
            # 群注册只接受群聊提交（绑定当前群 openid）
            await bot.send(event, "群注册请在目标群里发送：" + GROUP_REGISTER_USAGE)
            return

        await _handle_group_register(bot, event, message)


async def _handle_user_register(bot: QQBot, event: C2CMessageCreateEvent, message):

    parts = message.split(maxsplit=2)

    if len(parts) < 3:
        await bot.send(event, USER_REGISTER_USAGE)
        return

    qq_id = parts[1].strip()
    nickname = parts[2].strip()

    if not qq_id.isdigit() or not nickname:
        await bot.send(event, USER_REGISTER_USAGE)
        return

    openid = event.author.user_openid

    register_user(openid, qq_id, nickname)

    logger.info(
        "[QQ 用户注册] openid={} qq={} 昵称={}",
        openid,
        qq_id,
        nickname,
    )

    await bot.send(
        event,
        "注册申请已提交，等待管理员审核（审核通过后仍需管理员启用）"
    )


async def _handle_group_register(bot: QQBot, event: GroupMessageCreateEvent, message):

    parts = message.split(maxsplit=2)

    if len(parts) < 3:
        await bot.send(event, GROUP_REGISTER_USAGE)
        return

    qq_id = parts[1].strip()
    group_name = parts[2].strip()

    if not qq_id.isdigit() or not group_name:
        await bot.send(event, GROUP_REGISTER_USAGE)
        return

    # 权限校验：仅白名单用户可提交群注册（qq_user_openids 唯一权限来源）
    operator_openid = event.author.member_openid

    if operator_openid not in get_active_user_openids():

        logger.info(
            "拒绝非白名单用户群注册: {} @ 群 {}",
            operator_openid,
            event.group_openid,
        )

        await bot.send(event, "仅白名单用户可提交群注册申请")
        return

    register_group(event.group_openid, qq_id, group_name, operator_openid)

    logger.info(
        "[QQ 群注册] group_openid={} 群号={} 群名={} 操作人={}",
        event.group_openid,
        qq_id,
        group_name,
        operator_openid,
    )

    await bot.send(
        event,
        "群注册申请已提交，等待管理员审核（审核通过后仍需管理员启用）"
    )
