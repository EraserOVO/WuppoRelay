@echo off
chcp 65001 >nul
rem ============================================================
rem  WuppoRelay 一键安装脚本（Windows）
rem  1. 创建虚拟环境 .venv
rem  2. 安装项目依赖（pip install -e .）
rem  3. 缺少 .env.prod 时从 .env.example 复制生成
rem  完成后请编辑 .env.prod，再双击「启动面板.bat」
rem ============================================================
cd /d "%~dp0"

rem ---- 检查 python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.10 或更高版本，
    echo        安装时记得勾选 "Add to PATH"
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [错误] 需要 Python 3.10 或更高版本，当前版本：
    python --version
    pause
    exit /b 1
)

echo [1/3] 创建虚拟环境 .venv ...
python -m venv .venv
if errorlevel 1 (
    echo [错误] 创建虚拟环境失败
    pause
    exit /b 1
)

echo [2/3] 安装依赖（首次需要数分钟，请耐心等待）...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重新运行本脚本
    pause
    exit /b 1
)

echo [3/3] 检查配置文件 .env.prod ...
if not exist ".env.prod" (
    copy /y ".env.example" ".env.prod" >nul
    echo     已从 .env.example 生成 .env.prod
) else (
    echo     .env.prod 已存在，跳过
)

echo.
echo ============================================================
echo  安装完成！接下来：
echo   1. 用记事本打开 .env.prod，填入你自己的机器人配置
echo      （每项含义见文件内注释，或 docs\SETUP.md）
echo   2. 双击「启动面板.bat」打开管理面板
echo   3. 在面板中勾选你的 Discord 频道与 QQ 群，点击「启动机器人」
echo ============================================================
pause
