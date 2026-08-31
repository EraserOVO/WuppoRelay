# -*- coding: utf-8 -*-
"""
WuppoRelay 管理面板（无窗口运行，pythonw 启动）

功能：
  - 机器人 启动 / 停止 / 重启
  - 管理哪些 QQ 群接收转发、哪些 Discord 频道参与转发
  - 实时查看机器人运行日志

启动方式：
  双击快捷方式「管理面板」，或：
    pythonw "C:/Users/Era/Wuppo/panel/管理面板.pyw"
"""

import os
import sys
import json
import time
import ctypes
import socket
import threading
import subprocess
import webbrowser

# ---------------- 路径 ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))      # .../panel
PROJECT_ROOT = os.path.dirname(BASE_DIR)                    # .../Wuppo
os.chdir(PROJECT_ROOT)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
LOG_DIR = os.path.join(DATA_DIR, "logs")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
BOT_LOG = os.path.join(LOG_DIR, "bot.log")
PANEL_LOG = os.path.join(LOG_DIR, "panel.log")
PID_FILE = os.path.join(DATA_DIR, "bot.pid")
RUNTIME_LEVEL_FILE = os.path.join(DATA_DIR, "runtime_log_level.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
BOT_ENTRY = os.path.join(PROJECT_ROOT, "bot.py")

PANEL_HOST = "127.0.0.1"
PANEL_PORT = 8090
BOT_PORT = 8082

# ---------------- 转发模式预设 ----------------
# 测试模式：仅启用测试群 + 测试频道
# 转发模式：启用除测试群/频道以外的全部群聊与频道
# 自定义模式：其它任意勾选组合
#
# 测试群/频道 ID 不硬编码，从 data/settings.json 读取：
#   test_group_openid / test_channel_id（可分发；未配置时测试/转发模式不可用）

# ---------------- 开机自启 ----------------
# 通过启动文件夹里的 WuppoRelayAutostart.vbs（隐藏窗口）运行 panel/autostart.pyw，
# autostart.pyw 追加 --autostart 标志后调用本面板 main()：
#   后台启动面板(不开浏览器) + 自动拉起机器人
AUTOSTART_VBS_NAME = "WuppoRelayAutostart.vbs"
AUTOSTART_FLAG = "--autostart"

os.makedirs(LOG_DIR, exist_ok=True)

# pythonw 下没有控制台窗口，把面板自身的输出重定向到 panel.log
try:
    _panel_log = open(PANEL_LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = _panel_log
    sys.stderr = _panel_log
except Exception:
    pass

sys.path.insert(0, PROJECT_ROOT)


def _log(msg):
    """面板运行日志（panel.log），带时间戳"""
    try:
        with open(PANEL_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# ---------------- 配置读写 ----------------
def load_settings():
    default = get_default_settings()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("qq_group_openids"), list):
            data["qq_group_openids"] = default["qq_group_openids"]
        if not isinstance(data.get("discord_channels"), list):
            data["discord_channels"] = default["discord_channels"]
        return data
    except Exception:
        return default


def get_default_settings():
    try:
        from plugins.config import DISCORD_CHANNELS, QQ_GROUP_OPENIDS
    except Exception:
        return {
            "qq_group_openids": [],
            "discord_channels": [],
            "backfill_enabled": True,
            "backfill_limit": 10,
            "test_group_openid": "",
            "test_channel_id": "",
            "qq_appid": "",
        }
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
        "discord_channels": channels,
        "backfill_enabled": True,
        "backfill_limit": 10,
        "test_group_openid": "",
        "test_channel_id": "",
        "qq_appid": "",
    }


def save_settings(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)


def get_test_ids():
    """返回 (test_group_openid, test_channel_id)；未配置返回 (None, None)"""
    data = load_settings()
    gid = str(data.get("test_group_openid") or "").strip()
    cid = str(data.get("test_channel_id") or "").strip()
    return (gid or None), (cid or None)


def get_qq_appid():
    """返回 QQ 开放平台 AppID（settings.json 的 qq_appid）；未配置返回 "" """
    data = load_settings()
    return str(data.get("qq_appid") or "").strip()


def classify_mode(data):
    """根据 settings 的勾选状态判断当前组合匹配哪种预设（状态推导）；
    未配置测试群/频道时无法匹配测试/转发预设，一律视为自定义"""
    groups = data.get("qq_group_openids", [])
    channels = data.get("discord_channels", [])

    test_group, test_channel = get_test_ids()

    if not test_group or not test_channel:
        return "custom"

    enabled_g = {str(g.get("openid")) for g in groups if g.get("enabled")}
    enabled_c = {str(c.get("id")) for c in channels if c.get("enabled")}

    if enabled_g == {test_group} and enabled_c == {test_channel}:
        return "test"

    known_g = {str(g.get("openid")) for g in groups}
    known_c = {str(c.get("id")) for c in channels}
    if (
        enabled_g == known_g - {test_group}
        and enabled_c == known_c - {test_channel}
    ):
        return "forward"

    return "custom"


def effective_mode(data):
    """展示用模式：
    - 用户点过「自定义模式」后，即使勾选组合仍匹配测试/转发预设，也始终显示自定义；
    - 勾选组合不匹配任何预设时，自动落到自定义模式。
    """
    state = classify_mode(data)
    if data.get("mode") == "custom":
        return "custom"
    return state


def apply_mode(mode):
    """应用模式预设，返回 (ok, msg, settings)
    - test / forward：改勾选组合，并记录所选模式
      （未配置测试群/频道时 test 不可用，forward 退化为全部启用）
    - custom：不改勾选组合，仅记录为自定义模式
    """
    data = load_settings()

    test_group, test_channel = get_test_ids()

    if mode == "test":
        if not test_group or not test_channel:
            return (
                False,
                "未配置测试群/频道（settings.json 的 test_group_openid / test_channel_id）",
                data,
            )
        for g in data["qq_group_openids"]:
            g["enabled"] = (str(g.get("openid")) == test_group)
        for c in data["discord_channels"]:
            c["enabled"] = (str(c.get("id")) == test_channel)
        data["mode"] = "test"
    elif mode == "forward":
        for g in data["qq_group_openids"]:
            g["enabled"] = (
                (str(g.get("openid")) != test_group) if test_group else True
            )
        for c in data["discord_channels"]:
            c["enabled"] = (
                (str(c.get("id")) != test_channel) if test_channel else True
            )
        data["mode"] = "forward"
    elif mode == "custom":
        # 自定义模式不做任何勾选改动，仅记录选择
        data["mode"] = "custom"
    else:
        return False, "未知模式", data

    save_settings(data)
    return True, "", data


# ---------------- 机器人进程管理 ----------------
_bot = {"proc": None}

# 看门狗：_want_running 为 True 表示"面板期望机器人保持运行"（点过启动/重启/开机自启），
# 机器人崩溃或被杀后由后台线程自动拉起；手动停止（stop_bot）会置 False，不再自动重启。
_want_running = False
_last_auto_start = 0.0
WATCHDOG_INTERVAL = 10.0   # 每 10 秒检查一次机器人存活
MIN_AUTO_RESTART_GAP = 30.0  # 两次自动重启至少间隔 30 秒，连续崩溃时不刷屏


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except Exception:
        return False


PANEL_PING_URL = "http://%s:%d/api/status" % (PANEL_HOST, PANEL_PORT)


def panel_alive():
    """判断面板是否真正在运行：HTTP 探测 /api/status 成功才算。

    不能只用 TCP 探测（port_open）：一旦 8090 被其它进程（如系统里
    恰好占用的无关软件）监听，新面板会被误判为"面板已在运行"而直接
    退出，浏览器反而打不开管理页。只有返回 200 的 HTTP 响应才说明
    当前 8090 上确实是本面板。
    """
    import urllib.request
    try:
        with urllib.request.urlopen(PANEL_PING_URL, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _proc_exists(pid):
    """判断 pid 是否仍在运行。
    用 WaitForSingleObject 检测进程是否已 signaled（终止）：
    即使其它进程（如面板持有的 Popen 句柄）仍持有该进程的对象句柄，
    进程终止后 WaitForSingleObject 也会返回 signaled，
    避免"进程已退出却被误判为运行中"导致看门狗不重启。"""
    if pid <= 0:
        return False
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            # 0 = WAIT_OBJECT_0（进程已终止）；0x102 = WAIT_TIMEOUT（仍在运行）
            return (
                ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
                == 0x102
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return False


def read_pid():
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def write_pid(pid):
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def clear_pid():
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def bot_status():
    proc = _bot["proc"]
    if proc is not None and proc.poll() is None:
        return {"running": True, "pid": proc.pid, "managed": True}
    pid = read_pid()
    if pid:
        if _proc_exists(pid):
            return {"running": True, "pid": pid, "managed": False}
        # 过期 pid（重启后进程已不存在）自动清理，避免被其它进程复用 PID 误判
        clear_pid()
    if port_open(BOT_PORT):
        return {"running": True, "pid": 0, "managed": False}
    return {"running": False, "pid": 0, "managed": False}


def _kill_tree(pid):
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        try:
            ctypes.windll.kernel32.TerminateProcess(
                ctypes.windll.kernel32.OpenProcess(0x0001, False, pid), 1
            )
        except Exception:
            pass


def start_bot():
    global _want_running
    st = bot_status()
    if st["running"]:
        return {"ok": False, "msg": "机器人已在运行"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    # 日志改由 bot.py 内的 loguru 直接写 data/logs/bot.log（带 5MB 轮转），
    # 因此不再把 stdout/stderr 重定向到 bot.log，避免双写同一文件。
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.Popen(
            [VENV_PYTHON, BOT_ENTRY],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            env=env,
        )
    except Exception as exc:
        _log("启动机器人失败: %s" % exc)
        return {"ok": False, "msg": f"启动失败: {exc}"}
    _bot["proc"] = proc
    write_pid(proc.pid)
    _want_running = True
    _log("启动机器人 pid=%s" % proc.pid)
    return {"ok": True, "pid": proc.pid}


def stop_bot():
    global _want_running
    _want_running = False
    stopped = False
    proc = _bot["proc"]
    if proc is not None and proc.poll() is None:
        _kill_tree(proc.pid)
        stopped = True
    _bot["proc"] = None
    pid = read_pid()
    if pid and _proc_exists(pid):
        _kill_tree(pid)
        stopped = True
    clear_pid()
    if stopped:
        _log("停止机器人")
    return {"ok": True, "stopped": stopped}


def log_tail(path, n=400, max_bytes=262144):
    """从文件末尾读取最后 n 行（最多读 max_bytes 字节），
    避免整文件读入（bot.log 轮转后仍可能很大）"""
    if not os.path.exists(path):
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


# ---------------- 同步机器人自动发现的新群 ----------------
def sync_discovered_groups(data):
    path = os.path.join(DATA_DIR, "qq_group_openids.json")
    discovered = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        discovered = raw.get("group_openids", []) if isinstance(raw, dict) else []
    except Exception:
        discovered = []

    groups = data.setdefault("qq_group_openids", [])
    known = {str(g.get("openid", "")) for g in groups}
    added = 0
    for openid in discovered:
        openid = str(openid)
        if not openid or openid in known:
            continue
        groups.append(
            {"openid": openid, "enabled": False, "remark": "自动发现，点击启用"}
        )
        known.add(openid)
        added += 1
    return added


# ---------------- 开机自启 ----------------
def startup_dir():
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
    )


def autostart_vbs_path():
    return os.path.join(startup_dir(), AUTOSTART_VBS_NAME)


def get_autostart():
    return os.path.exists(autostart_vbs_path())


def set_autostart(enabled):
    """开启/关闭开机自启：向启动文件夹写/删 WuppoRelayAutostart.vbs
    内容全为 ASCII，避免中文路径在 VBS 里的编码问题（由 autostart.pyw 中转）"""
    path = autostart_vbs_path()
    if enabled:
        pyw = os.path.join(os.path.dirname(VENV_PYTHON), "pythonw.exe")
        launcher = os.path.join(BASE_DIR, "autostart.pyw")
        cmd = '"%s" "%s"' % (pyw, launcher)
        vbs = (
            'Set ws = CreateObject("Wscript.Shell")\r\n'
            'ws.Run "' + cmd.replace('"', '""') + '", 0, False\r\n'
        )
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="ascii") as f:
                f.write(vbs)
        except Exception as exc:
            _log("写入开机自启失败: %s" % exc)
            return False
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception as exc:
            _log("删除开机自启失败: %s" % exc)
            return False
    ok = os.path.exists(path) if enabled else not os.path.exists(path)
    _log("开机自启 -> %s" % ("开启" if enabled else "关闭"))
    return ok


def _ensure_bot_running():
    st = bot_status()
    if st["running"]:
        _log("机器人已在运行 pid=%s" % st["pid"])
        return
    if not panel_alive():
        result = start_bot()
        _log("自动启动机器人结果: %s" % result)
        return
    # 面板端口已就绪：通过面板 API 启动，确保由面板托管（managed=True）
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://%s:%d/api/bot/start" % (PANEL_HOST, PANEL_PORT),
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        _log("通过面板API启动机器人: %s" % result)
    except Exception as exc:
        _log("通过面板API启动机器人失败，改由本进程直接启动: %s" % exc)
        result = start_bot()
        _log("自动启动机器人结果: %s" % result)


def _autostart_bot_after_up():
    """后台自启模式：等面板端口起来后自动拉起机器人（不开浏览器）"""
    for _ in range(40):
        if panel_alive():
            break
        time.sleep(0.5)
    time.sleep(1)
    _ensure_bot_running()


def _watchdog_loop():
    """看门狗：_want_running（面板期望机器人运行）时，检测到机器人死了就自动拉起。
    手动停止后 _want_running=False 不会拉起；自动重启之间至少间隔 MIN_AUTO_RESTART_GAP，
    避免机器人反复崩溃时每 10 秒拉起一次的刷屏。"""
    global _last_auto_start
    while True:
        time.sleep(WATCHDOG_INTERVAL)
        if not _want_running:
            continue
        if bot_status()["running"]:
            continue
        if time.time() - _last_auto_start < MIN_AUTO_RESTART_GAP:
            continue
        _log("看门狗：检测到机器人已停止，自动重启")
        result = start_bot()
        if result.get("ok"):
            _last_auto_start = time.time()
            _log("看门狗自动重启成功 pid=%s" % result.get("pid"))
        else:
            _log("看门狗自动重启失败: %s" % result.get("msg"))


# ---------------- 转发统计 / 日志级别（机器人侧写入，面板只读） ----------------
def read_stats():
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def current_log_level():
    try:
        with open(RUNTIME_LEVEL_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("level", "")
    except Exception:
        return ""


# ---------------- bot HTTP 转发（面板 -> bot 8082） ----------------
# bot 的 NoneBot FastAPI 运行在 127.0.0.1:8082，补发相关查询/操作
# （待补发数量、手动补发、清除待补发）由 bot 进程完成，面板只做转发。
BOT_API_BASE = "http://127.0.0.1:%d" % BOT_PORT


def bot_api_get(path, timeout=10.0):
    """GET bot HTTP 端点，返回 dict；bot 不可用时返回 None"""
    import urllib.request
    try:
        with urllib.request.urlopen(BOT_API_BASE + path, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def bot_api_post(path, timeout=30.0):
    """POST bot HTTP 端点（无请求体），返回 dict；bot 不可用时返回 None"""
    import urllib.request
    try:
        req = urllib.request.Request(
            BOT_API_BASE + path,
            data=b"",
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ---------------- FastAPI 面板 ----------------
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI()


# ---------------- Origin 校验 ----------------
# 面板只绑定 127.0.0.1，但无请求体的 POST（/api/bot/stop、/api/settings/reset 等）
# 可被任意网页用 mode:"no-cors" 的 fetch 触发（浏览器不发预检），从而停掉机器
# 人或重置配置。浏览器禁止自定义 Origin 头，故校验 Origin 即可堵住跨站请求：
# 只放行面板自身来源（http://127.0.0.1:8090）与无 Origin 的本地调用（curl/urllib）。
ALLOWED_ORIGIN = "http://%s:%s" % (PANEL_HOST, PANEL_PORT)


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and origin != ALLOWED_ORIGIN:
        return JSONResponse({"ok": False, "msg": "Forbidden"}, status_code=403)
    response = await call_next(request)
    # 面板是本地单页应用，禁止浏览器缓存页面/接口，
    # 否则改版后浏览器仍显示旧页面（曾导致"机器人状态检测中…"残留）
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE_HTML


@app.get("/api/status")
def api_status():
    st = bot_status()
    return {
        "running": st["running"],
        "pid": st["pid"],
        "managed": st["managed"],
        "mode": effective_mode(load_settings()),
        "autostart": get_autostart(),
        "log": log_tail(BOT_LOG),
        "stats": read_stats(),
        "level": current_log_level(),
    }


@app.get("/api/settings")
def api_settings():
    return load_settings()


@app.get("/api/settings/default")
def api_settings_default():
    return get_default_settings()


@app.post("/api/settings/save")
async def api_settings_save(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    save_settings(data)
    return {"ok": True, "settings": load_settings()}


@app.post("/api/settings/sync")
def api_settings_sync():
    data = load_settings()
    added = sync_discovered_groups(data)
    if added:
        save_settings(data)
    return {"ok": True, "added": added, "settings": load_settings()}


@app.post("/api/settings/reset")
def api_settings_reset():
    default = get_default_settings()
    save_settings(default)
    return {"ok": True, "settings": load_settings()}


@app.post("/api/mode/apply")
async def api_mode_apply(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    mode = (body or {}).get("mode")
    ok, msg, data = apply_mode(mode)
    if not ok:
        return JSONResponse({"ok": False, "msg": msg}, status_code=400)
    return {"ok": True, "mode": effective_mode(data), "settings": data}


@app.get("/api/autostart")
def api_autostart():
    return {"ok": True, "enabled": get_autostart()}


@app.post("/api/autostart/set")
async def api_autostart_set(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    enabled = bool((body or {}).get("enabled"))
    ok = set_autostart(enabled)
    return {"ok": ok, "enabled": get_autostart()}


@app.post("/api/bot/start")
def api_bot_start():
    return start_bot()


@app.post("/api/bot/stop")
def api_bot_stop():
    return stop_bot()


@app.post("/api/bot/restart")
def api_bot_restart():
    stop_bot()
    time.sleep(1.0)
    return start_bot()


@app.get("/api/bot/loglevel")
def api_bot_loglevel():
    return {"ok": True, "level": current_log_level()}


@app.post("/api/bot/loglevel")
async def api_bot_loglevel_set(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    level = str((body or {}).get("level", "")).upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        return JSONResponse({"ok": False, "msg": "无效的日志级别"}, status_code=400)
    try:
        with open(RUNTIME_LEVEL_FILE, "w", encoding="utf-8") as f:
            json.dump({"level": level}, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        _log("设置日志级别失败: %s" % exc)
        return {"ok": False, "msg": "写入失败"}
    _log("日志级别 -> %s" % level)
    return {"ok": True, "level": level}


@app.post("/api/bot/log/clear")
def api_bot_log_clear():
    try:
        with open(BOT_LOG, "w", encoding="utf-8"):
            pass
        _log("清空机器人日志")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}


# ---------------- 离线补发（转发到 bot 8082） ----------------
@app.get("/api/backfill/pending")
def api_backfill_pending():
    running = bot_status()["running"]
    if not running:
        return {"ok": False, "running": False, "msg": "机器人未运行"}
    data = bot_api_get("/api/backfill/pending")
    if data is None:
        return {"ok": False, "running": True, "msg": "bot 接口不可用"}
    data["running"] = True
    return data


@app.post("/api/backfill/run")
def api_backfill_run():
    if not bot_status()["running"]:
        return {"ok": False, "msg": "机器人未运行"}
    data = bot_api_post("/api/backfill/run")
    if data is None:
        return {"ok": False, "msg": "bot 接口不可用"}
    return data


@app.post("/api/backfill/clear")
def api_backfill_clear():
    if not bot_status()["running"]:
        return {"ok": False, "msg": "机器人未运行"}
    data = bot_api_post("/api/backfill/clear")
    if data is None:
        return {"ok": False, "msg": "bot 接口不可用"}
    return data


# ---------------- 页面 ----------------
PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WuppoRelay 管理面板</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px;
    font-family: "Microsoft YaHei", Arial, sans-serif;
    background: #1e2229; color: #e6e8eb;
  }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: #8b93a1; font-size: 13px; margin-bottom: 20px; }
  .card {
    background: #272c35; border-radius: 10px;
    padding: 16px 18px; margin-bottom: 16px;
  }
  .card h2 { font-size: 15px; margin: 0 0 12px; color: #aeb6ff; }
  .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .badge {
    padding: 4px 12px; border-radius: 20px; font-size: 13px; font-weight: bold;
  }
  .badge.on { background: #1f6f43; color: #b7f7c9; }
  .badge.off { background: #6b3b3b; color: #ffc9c9; }
  .pid { font-size: 13px; color: #8b93a1; }
  button {
    background: #5865F2; color: #fff; border: none; border-radius: 6px;
    padding: 8px 16px; font-size: 14px; cursor: pointer;
  }
  button:hover { opacity: .9; }
  button.sec { background: #2c3240; }
  button.danger { background: #c0392b; }
  button.warn { background: #d97706; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  button.mode { background: #2c3240; }
  button.mode.active { background: #5865F2; border-color: #aeb6ff; }
  button.mode.active::after { content: " ✓"; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid #363d49; }
  th { color: #8b93a1; font-weight: normal; font-size: 13px; }
  input[type=text] {
    background: #1a1f27; border: 1px solid #3a4250; color: #e6e8eb;
    border-radius: 5px; padding: 6px 8px; font-size: 14px; width: 100%;
  }
  input[type=checkbox] { width: 18px; height: 18px; }
  .mono { font-family: Consolas, monospace; font-size: 12px; }
  .log {
    background: #16191f; border-radius: 6px; padding: 10px;
    font-family: Consolas, monospace; font-size: 12px;
    white-space: pre-wrap; word-break: break-all; max-height: 260px; overflow: auto;
  }
  .addrow { display: flex; gap: 8px; margin-top: 10px; }
  .addrow input { flex: 1; }
  .addrow button { flex: none; }
  .hint { color: #8b93a1; font-size: 12px; margin-top: 8px; }
  .tip { color: #ffd479; font-size: 12px; }
  .addgrid { display: grid; gap: 8px; margin-top: 10px; align-items: center; }
  .addgrid input, .addgrid button { width: 100%; }
  .delbtn { width: 100%; }
</style>
</head>
<body>
  <h1>WuppoRelay 管理面板</h1>
  <div class="sub">Discord → QQ 实时转发 · 本地管理（127.0.0.1:8090）</div>

  <div class="card">
    <h2>机器人状态</h2>
    <div class="row">
      <span id="badge" class="badge off">检测中…</span>
      <span id="pidinfo" class="pid"></span>
      <span id="stats" class="pid"></span>
      <button id="btnStart">启动机器人</button>
      <button id="btnStop" class="danger">停止机器人</button>
      <button id="btnRestart" class="sec">重启机器人</button>
    </div>
    <div class="row" style="margin-top:10px;">
      <label style="display:flex; align-items:center; gap:8px; font-size:13px; cursor:pointer;">
        <input type="checkbox" id="chkAutostart" style="width:16px;height:16px;">
        开机自动启动机器人（后台运行，不打开面板）
      </label>
    </div>
    <div class="hint" id="managedHint"></div>
  </div>

  <div class="card">
    <h2>模式选择</h2>
    <div class="row">
      <button id="modeTest" class="mode">测试模式</button>
      <button id="modeForward" class="mode">转发模式</button>
      <button id="modeCustom" class="mode">自定义模式</button>
    </div>
    <div class="hint" id="modeHint"></div>
  </div>

  <div class="card">
    <h2>QQ 接收群</h2>
    <table style="table-layout:fixed">
      <thead><tr><th style="width:64px;text-align:center">启用</th><th>群 openid</th><th style="width:30%">备注</th><th style="width:100px"></th></tr></thead>
      <tbody id="groupBody"></tbody>
    </table>
    <div class="addgrid" style="grid-template-columns:64px 1fr 30% 100px;">
      <div></div>
      <input id="newOpenid" placeholder="粘贴群 openid（机器人在群里 @ 后可从日志/qq_group_openids.json 获取）">
      <input id="newRemark" placeholder="备注（可选）">
      <button id="btnAddGroup">添加</button>
    </div>
    <div class="row addrow">
      <button id="btnSync" class="sec">同步自动发现的群</button>
      <button id="btnQQPlatform" class="sec">QQ 开放平台管理页</button>
      <button id="btnReset" class="warn" style="margin-left:auto">⚠ 恢复默认配置</button>
    </div>
    <div class="hint">勾选 = 该群接收 Discord 转发消息；修改后立即生效，无需重启。</div>
  </div>

  <div class="card">
    <h2>Discord 转发频道</h2>
    <table style="table-layout:fixed">
      <thead><tr><th style="width:64px;text-align:center">启用</th><th>频道名称</th><th>频道 ID</th><th style="width:100px"></th></tr></thead>
      <tbody id="chanBody"></tbody>
    </table>
    <div class="addgrid" style="grid-template-columns:64px 1fr 1fr 100px;">
      <div></div>
      <input id="newChanName" placeholder="频道名称">
      <input id="newChanId" placeholder="频道 ID">
      <button id="btnAddChan">添加</button>
    </div>
    <div class="hint">勾选 = 该频道的消息会被转发到已启用的 QQ 群；修改后立即生效。</div>
  </div>

  <div class="card">
    <h2>离线补发</h2>
    <div class="row" style="gap:20px;align-items:center;flex-wrap:wrap;">
      <label class="row" style="gap:8px;align-items:center;cursor:pointer;">
        <input type="checkbox" id="backfillEnabled" style="width:16px;height:16px;">
        <span>启用补发</span>
      </label>
      <label class="row" style="gap:8px;align-items:center;">
        <span>单次补发上限</span>
        <input type="number" id="backfillLimit" min="1" step="1" style="width:90px;background:#1a1f27;border:1px solid #3a4250;color:#e6e8eb;border-radius:5px;padding:6px 8px;font-size:13px;">
        <span>条/频道</span>
      </label>
      <span class="row" style="gap:8px;align-items:center;margin-left:auto;">
        <span>待补发</span>
        <b id="backfillPending">—</b>
      </span>
    </div>
    <div class="row" style="margin-top:10px;">
      <button id="btnBackfillRun" class="sec">立即补发</button>
      <button id="btnBackfillClear" class="warn" style="margin-left:auto">清除待补发</button>
    </div>
    <div class="hint">启用补发后，机器人每次连接会补发离线期间未转发的消息（旧消息优先，逐批补发，每批不超过上限）。清除待补发会把记录推进到最新消息，未转发的旧消息将不再补发。</div>
  </div>

  <div class="card">
    <h2>运行日志</h2>
    <pre id="log" class="log">加载中…</pre>
    <div class="row" style="margin-top:10px;">
      <select id="logLevel" style="background:#1a1f27;border:1px solid #3a4250;color:#e6e8eb;border-radius:5px;padding:6px 8px;font-size:13px;">
        <option value="DEBUG">DEBUG</option>
        <option value="INFO">INFO</option>
        <option value="WARNING">WARNING</option>
        <option value="ERROR">ERROR</option>
      </select>
      <button id="btnLogLevel" class="sec">应用日志级别</button>
      <button id="btnClearLog" class="sec">清空日志</button>
    </div>
  </div>

<script>
let settings = null;

// QQ 开放平台「我的机器人」管理页，AppID 从 settings.qq_appid 读取（未配置时按钮隐藏）
function qqPlatformUrl() {
  var appid = settings && settings.qq_appid ? String(settings.qq_appid) : "";
  return appid ? "https://q.qq.com/qqbot/dashboard/manage/" + appid : "";
}

async function jget(url) {
  const r = await fetch(url);
  return r.json();
}
async function jpost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

function renderStatus(st) {
  const b = document.getElementById("badge");
  if (st.running) {
    b.textContent = "运行中";
    b.className = "badge on";
    document.getElementById("btnStart").disabled = true;
    document.getElementById("btnStop").disabled = false;
    document.getElementById("btnRestart").disabled = false;
  } else {
    b.textContent = "已停止";
    b.className = "badge off";
    document.getElementById("btnStart").disabled = false;
    document.getElementById("btnStop").disabled = true;
    document.getElementById("btnRestart").disabled = true;
  }
  document.getElementById("pidinfo").textContent = st.pid ? ("PID: " + st.pid) : "";
  const stats = st.stats || {};
  const statsEl = document.getElementById("stats");
  statsEl.textContent = (stats.total_forwarded || stats.total_failed)
    ? ("今日转发 " + (stats.today_forwarded || 0) + " 条 · 累计 " + (stats.total_forwarded || 0) + " 条 · 失败 " + (stats.total_failed || 0) + " 条")
    : "";
  const lvlEl = document.getElementById("logLevel");
  if (lvlEl && st.level) lvlEl.value = st.level;
  const hint = document.getElementById("managedHint");
  hint.textContent = st.running && !st.managed
    ? "检测到机器人可能由其他方式启动（本面板无法完全控制），如需面板接管请先手动停止它。"
    : "";
  document.getElementById("log").textContent = st.log || "（暂无日志）";
  const el = document.getElementById("log");
  el.scrollTop = el.scrollHeight;
  renderMode(st.mode);
  var autoEl = document.getElementById("chkAutostart");
  if (autoEl) autoEl.checked = !!st.autostart;
}

var MODE_INFO = {
  test:     { btn: "modeTest",     name: "测试模式", hint: "仅启用测试群与测试频道" },
  forward:  { btn: "modeForward",  name: "转发模式", hint: "启用除测试群/频道以外的全部群聊与频道" },
  custom:   { btn: "modeCustom",   name: "自定义模式", hint: "按当前手动勾选配置" }
};

function renderMode(mode) {
  var info = MODE_INFO[mode] || MODE_INFO.custom;
  var hasTestIds = !!(settings && settings.test_group_openid && settings.test_channel_id);
  ["modeTest", "modeForward"].forEach(function (id) {
    document.getElementById(id).disabled = !hasTestIds;
  });
  ["modeTest", "modeForward", "modeCustom"].forEach(function (id) {
    document.getElementById(id).classList.toggle("active", id === info.btn);
  });
  document.getElementById("modeHint").textContent =
    "当前模式：" + info.name + " · " + info.hint +
    (hasTestIds ? "" : " · 未配置测试群/频道，仅自定义模式可用");
}

function renderSettings() {
  const gb = document.getElementById("groupBody");
  gb.innerHTML = "";
  settings.qq_group_openids.forEach((g, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td style="text-align:center"><input type="checkbox" data-kind="group" data-i="' + i + '"' + (g.enabled ? " checked" : "") + '></td>' +
      '<td class="mono">' + escapeHtml(g.openid) + '</td>' +
      '<td><input type="text" value="' + escapeHtml(g.remark || "") + '" data-kind="groupRemark" data-i="' + i + '"></td>' +
      '<td><button class="sec delbtn" data-kind="groupDel" data-i="' + i + '">删除</button></td>';
    gb.appendChild(tr);
  });
  const cb = document.getElementById("chanBody");
  cb.innerHTML = "";
  settings.discord_channels.forEach((c, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td style="text-align:center"><input type="checkbox" data-kind="chan" data-i="' + i + '"' + (c.enabled ? " checked" : "") + '></td>' +
      '<td>' + escapeHtml(c.name || "") + '</td>' +
      '<td class="mono">' + escapeHtml(c.id) + '</td>' +
      '<td><button class="sec delbtn" data-kind="chanDel" data-i="' + i + '">删除</button></td>';
    cb.appendChild(tr);
  });
  // 离线补发设置
  const bfEnabled = document.getElementById("backfillEnabled");
  if (bfEnabled) bfEnabled.checked = settings.backfill_enabled !== false;
  const bfLimit = document.getElementById("backfillLimit");
  if (bfLimit) bfLimit.value = Number(settings.backfill_limit) > 0 ? settings.backfill_limit : 10;
  // QQ 开放平台按钮：未配置 AppID 时隐藏
  const qqBtn = document.getElementById("btnQQPlatform");
  if (qqBtn) qqBtn.style.display = qqPlatformUrl() ? "" : "none";
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
  });
}

function renderBackfill(data) {
  const el = document.getElementById("backfillPending");
  if (!el) return;
  if (!data || !data.ok) {
    el.textContent = data && data.running === false ? "机器人未运行" : "—";
    return;
  }
  const total = data.total || 0;
  el.textContent = total > 0 ? ("共 " + total + " 条") : "无";
  el.style.color = total > 0 ? "#e5b567" : "";
}

async function refreshBackfill() {
  try {
    const r = await jget("/api/backfill/pending");
    renderBackfill(r);
  } catch (err) {
    renderBackfill(null);
  }
}

async function confirmReset() {
  let impact = "（获取默认配置失败，请确认后谨慎操作）";
  try {
    const def = await jget("/api/settings/default");
    const s = settings || {qq_group_openids: [], discord_channels: []};
    const dg = def.qq_group_openids || [];
    const dc = def.discord_channels || [];
    const gids = {}, cids = {};
    dg.forEach(function (g) { gids[String(g.openid)] = true; });
    dc.forEach(function (c) { cids[String(c.id)] = true; });
    const gLost = (s.qq_group_openids || []).filter(function (g) { return !gids[String(g.openid)]; });
    const cLost = (s.discord_channels || []).filter(function (c) { return !cids[String(c.id)]; });
    const lines = [];
    if (gLost.length) {
      lines.push("• 将被移除的群：" + gLost.map(function (g) {
        return (g.remark || "未命名群") + "（" + g.openid + "）";
      }).join("、"));
    }
    if (cLost.length) {
      lines.push("• 将被移除的频道：" + cLost.map(function (c) {
        return (c.name || "未命名频道") + "（" + c.id + "）";
      }).join("、"));
    }
    if (!gLost.length && !cLost.length) {
      lines.push("• 当前群/频道列表与默认配置一致，没有需要移除的项");
    }
    impact = lines.join("\\n");
  } catch (err) { /* 保留默认 impact */ }
  const ok = confirm(
    "⚠ 恢复默认配置（慎用）\\n" +
    "此操作会将 data/settings.json 整体恢复为出厂默认值，不可撤销：\\n" +
    "• QQ 接收群 / Discord 转发频道列表重置为默认配置（仅保留默认的测试群与默认频道），当前启用的其它群/频道会被移除\\n" +
    "• 日志级别、开机自启、统计数据、机器人进程均不受影响，不会自动重启\\n" +
    impact + "\\n\\n确定恢复默认配置？"
  );
  if (!ok) return;
  const r = await jpost("/api/settings/reset");
  settings = r.settings;
  renderSettings();
}

async function saveAndReload() {
  await jpost("/api/settings/save", settings);
  settings = await jget("/api/settings");
  renderSettings();
}

document.addEventListener("click", async function (e) {
  const btn = e.target.closest("button");
  if (!btn) return;
  const k = btn.dataset.kind;
  if (k === "groupDel") {
    settings.qq_group_openids.splice(Number(btn.dataset.i), 1);
    await saveAndReload();
  } else if (k === "chanDel") {
    settings.discord_channels.splice(Number(btn.dataset.i), 1);
    await saveAndReload();
  } else if (btn.id === "btnAddGroup") {
    const oid = document.getElementById("newOpenid").value.trim();
    if (!oid) return alert("请填写群 openid");
    settings.qq_group_openids.push({openid: oid, enabled: true, remark: document.getElementById("newRemark").value.trim()});
    document.getElementById("newOpenid").value = "";
    document.getElementById("newRemark").value = "";
    await saveAndReload();
  } else if (btn.id === "btnAddChan") {
    const id = document.getElementById("newChanId").value.trim();
    if (!id) return alert("请填写频道 ID");
    settings.discord_channels.push({id: id, name: document.getElementById("newChanName").value.trim(), enabled: true});
    document.getElementById("newChanId").value = "";
    document.getElementById("newChanName").value = "";
    await saveAndReload();
  } else if (btn.id === "btnSync") {
    const r = await jpost("/api/settings/sync");
    alert(r.added ? ("已同步 " + r.added + " 个新群（默认未启用）") : "没有发现新群");
    settings = r.settings;
    renderSettings();
  } else if (btn.id === "btnReset") {
    await confirmReset();
  } else if (btn.id === "btnQQPlatform") {
    var url = qqPlatformUrl();
    if (url) window.open(url, "_blank");
    else alert("未配置 QQ AppID（settings.json 的 qq_appid）");
  } else if (btn.id === "btnLogLevel") {
    const level = document.getElementById("logLevel").value;
    const r = await jpost("/api/bot/loglevel", {level: level});
    if (!r.ok) alert(r.msg || "设置失败");
    await refreshStatus();
  } else if (btn.id === "btnClearLog") {
    const r = await jpost("/api/bot/log/clear");
    if (!r.ok) alert(r.msg || "清空失败");
    await refreshStatus();
  } else if (btn.id === "btnBackfillRun") {
    const r = await jpost("/api/backfill/run");
    alert(r.ok ? (r.msg || "补发已触发") : (r.msg || "补发失败"));
    await refreshBackfill();
  } else if (btn.id === "btnBackfillClear") {
    if (!confirm("清除待补发？未转发的旧消息将不再补发，此操作不可撤销。")) return;
    const r = await jpost("/api/backfill/clear");
    alert(r.ok ? ("已清除 " + (r.cleared || 0) + " 个频道的待补发") : (r.msg || "清除失败"));
    await refreshBackfill();
  } else if (btn.id === "btnStart") {
    await jpost("/api/bot/start");
    await sleep(800); await refreshStatus();
  } else if (btn.id === "btnStop") {
    await jpost("/api/bot/stop");
    await sleep(800); await refreshStatus();
  } else if (btn.id === "btnRestart") {
    await jpost("/api/bot/restart");
    await sleep(1500); await refreshStatus();
  } else if (btn.id === "modeTest" || btn.id === "modeForward" || btn.id === "modeCustom") {
    const mode = {modeTest: "test", modeForward: "forward", modeCustom: "custom"}[btn.id];
    const r = await jpost("/api/mode/apply", {mode: mode});
    if (r.ok) {
      settings = r.settings;
      renderSettings();
    }
    await refreshStatus();
  }
});

document.addEventListener("change", async function (e) {
  const el = e.target;
  if (el.id === "chkAutostart") {
    const r = await jpost("/api/autostart/set", {enabled: el.checked});
    if (!r.ok) alert("设置开机自启失败");
    el.checked = !!r.enabled;
    return;
  }
  if (el.id === "backfillEnabled") {
    settings.backfill_enabled = el.checked;
    await saveAndReload();
    return;
  }
  if (el.id === "backfillLimit") {
    let v = parseInt(el.value, 10);
    if (!(v > 0)) v = 10;
    settings.backfill_limit = v;
    el.value = v;
    await saveAndReload();
    return;
  }
  if (!el.dataset.kind) return;
  if (el.dataset.kind === "group") {
    settings.qq_group_openids[Number(el.dataset.i)].enabled = el.checked;
    await saveAndReload();
  } else if (el.dataset.kind === "chan") {
    settings.discord_channels[Number(el.dataset.i)].enabled = el.checked;
    await saveAndReload();
  }
});

document.addEventListener("blur", async function (e) {
  const el = e.target;
  if (el.dataset.kind === "groupRemark") {
    settings.qq_group_openids[Number(el.dataset.i)].remark = el.value;
    await saveAndReload();
  }
}, true);

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

async function refreshStatus() {
  try {
    const st = await jget("/api/status");
    renderStatus(st);
  } catch (err) {
    document.getElementById("badge").textContent = "面板连接断开";
  }
}

async function init() {
  try {
    settings = await jget("/api/settings");
    renderSettings();
  } catch (err) { /* ignore */ }
  await refreshStatus();
  setInterval(refreshStatus, 3000);
  await refreshBackfill();
  setInterval(refreshBackfill, 30000);
}

init();
</script>
</body>
</html>
"""


# ---------------- 启动 ----------------
def _open_browser():
    time.sleep(1.2)
    try:
        webbrowser.open("http://%s:%d/" % (PANEL_HOST, PANEL_PORT))
    except Exception:
        pass


def main():
    autostart = AUTOSTART_FLAG in sys.argv

    _log("面板启动 autostart=%s" % autostart)

    # 清理过期的 bot.pid（重启后残留 PID）
    pid = read_pid()
    if pid and not _proc_exists(pid):
        clear_pid()
        _log("清理过期 bot.pid: %s" % pid)

    if autostart:
        # 开机自启：后台运行，不弹浏览器
        if panel_alive():
            # 面板已由其它实例启动：只需确保机器人在跑
            _log("面板已在运行，仅确保机器人启动")
            _ensure_bot_running()
            return
        threading.Thread(target=_autostart_bot_after_up, daemon=True).start()
    else:
        # 正常双击打开
        if panel_alive():
            # 面板已在运行，只需打开浏览器
            webbrowser.open("http://%s:%d/" % (PANEL_HOST, PANEL_PORT))
            return
        threading.Thread(target=_open_browser, daemon=True).start()

    # 面板接管时若机器人已在运行，视为期望运行：看门狗继续守护，崩溃后自动拉起
    if bot_status()["running"]:
        _want_running = True
    # 看门狗：面板期望机器人运行时，崩溃/被杀后自动拉起
    threading.Thread(target=_watchdog_loop, daemon=True).start()

    try:
        uvicorn.run(app, host=PANEL_HOST, port=PANEL_PORT, log_level="info")
    except Exception as exc:
        _log("面板启动失败: %s" % exc)
        if not autostart:
            # 双击容错：端口被占（面板已由其它实例启动）时也打开浏览器
            try:
                webbrowser.open("http://%s:%d/" % (PANEL_HOST, PANEL_PORT))
            except Exception:
                pass


if __name__ == "__main__":
    main()
