@echo off
chcp 65001 >nul
rem 启动 WuppoRelay 管理面板（无控制台窗口，自动打开浏览器）
rem 使用相对路径，可放在项目任意位置双击运行
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [错误] 未找到虚拟环境 .venv，请先双击运行 install.bat
    pause
    exit /b 1
)

if not exist "panel\管理面板.pyw" (
    echo [错误] 未找到 panel\管理面板.pyw
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "panel\管理面板.pyw"
exit /b 0
