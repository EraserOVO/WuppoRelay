import json
import os

from nonebot import get_driver
from nonebot import logger


DATA_FILE = "data/discord_history.json"


def load_last_ids():

    if not os.path.exists(DATA_FILE):
        return {}

    with open(
        DATA_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)



def save_last_ids(data):

    os.makedirs(
        "data",
        exist_ok=True
    )

    with open(
        DATA_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False
        )



# ==================================================
# 历史补发功能暂时停用
#
# 原因：
# 1. 实时 relay.py 已经可以正确处理图片/附件/embed
# 2. 历史 API 获取的数据结构不完整
# 3. 自动启动扫描会造成旧消息重复补发
#
# 保留此文件，未来重构历史同步功能
# ==================================================


async def check_history():

    logger.info(
        "Discord历史补发功能已暂停"
    )


driver = get_driver()


@driver.on_bot_connect
async def startup(bot):

    await check_history()
