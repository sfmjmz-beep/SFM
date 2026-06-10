@echo off
setlocal
cd /d %~dp0

echo ========================================
echo 京东商智报表工具 - 安装依赖
echo ========================================
python --version >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 Python。请先安装 Python 3.10 或更高版本，并勾选 Add Python to PATH。
  pause
  exit /b 1
)

if not exist .venv (
  echo [1/4] 创建虚拟环境 .venv ...
  python -m venv .venv
  if errorlevel 1 goto failed
)

echo [2/4] 升级 pip ...
call .venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto failed

echo [3/4] 安装 Python 依赖 ...
call .venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo [4/4] 安装 Playwright Chromium 浏览器 ...
call .venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto failed

echo.
echo [完成] 依赖安装成功。请双击 run.bat 启动工具。
pause
exit /b 0

:failed
echo.
echo [错误] 安装失败。请检查网络、Python 版本，或查看上方错误信息。
pause
exit /b 1
