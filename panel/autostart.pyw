# -*- coding: utf-8 -*-
"""
开机自启中转脚本（文件名纯 ASCII，避免启动项里的中文编码问题）。

由启动文件夹里的 WuppoRelayAutostart.vbs 隐藏窗口运行本脚本，
本脚本给当前进程追加 --autostart 标志后，调用管理面板的 main()：
  - 后台启动面板（不弹浏览器）
  - 面板起来后自动拉起机器人
"""

import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

sys.argv.append("--autostart")

import importlib

panel = importlib.import_module("管理面板")
panel.main()
