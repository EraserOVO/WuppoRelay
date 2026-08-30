from nonebot import on_command
from nonebot.adapters import Bot, Event


hello = on_command("hello", aliases={"你好"})


@hello.handle()
async def handle(bot: Bot, event: Event):
    await hello.finish("你好，我已经上线了！")