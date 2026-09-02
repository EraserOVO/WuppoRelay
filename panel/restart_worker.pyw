# -*- coding: utf-8 -*-
"""
面板重启中转脚本。

由管理面板 /api/panel/restart 以独立进程方式拉起：
  1. 等待旧面板退出并释放 127.0.0.1:8090（最多 15 秒）
  2. 按当前面板的启动方式（autostart.pyw 或 管理面板.pyw）重新拉起
旧面板在返回重启响应后自行退出，因此本脚本只需轮询端口即可，
避免新旧进程抢 8090 导致新面板起不来。
"""

import os
import socket
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE)

PANEL_HOST = "127.0.0.1"
PANEL_PORT = 8090

AUTOSTART_FLAG = "--autostart"
AUTOSTART = AUTOSTART_FLAG in sys.argv


def _log(msg):
    try:
        log_path = os.path.join(PROJECT_ROOT, "data", "logs", "panel.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _port_free(timeout=0.3):
    """返回 True 表示端口已释放（旧面板已退出）"""
    try:
        with socket.create_connection((PANEL_HOST, PANEL_PORT), timeout=timeout):
            return False
    except Exception:
        return True


def main():
    # 旧面板返回响应后延迟 os._exit，这里最多等 15 秒
    for _ in range(50):
        if _port_free():
            break
        time.sleep(0.3)

    entry = os.path.join(
        BASE,
        "autostart.pyw" if AUTOSTART else "管理面板.pyw",
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    try:
        subprocess.Popen(
            [sys.executable, entry],
            cwd=PROJECT_ROOT,
            creationflags=flags,
            env=env,
        )
        _log("面板重启：已拉起新进程 %s" % os.path.basename(entry))
    except Exception as exc:
        _log("面板重启拉起失败: %s" % exc)


if __name__ == "__main__":
    main()