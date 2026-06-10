@echo off
setlocal
cd /d %~dp0

echo ========================================
echo 京东商智报表工具 - 启动
echo ========================================
if exist .venv\Scripts\python.exe (
  set PY=.venv\Scripts\python.exe
) else (
  set PY=python
)

%PY% -c "import importlib.util,sys; missing=[m for m in ['pandas','openpyxl','playwright','customtkinter'] if importlib.util.find_spec(m) is None]; print('缺少依赖：'+', '.join(missing)+'\n请先运行 install.bat 安装依赖。') if missing else None; sys.exit(2 if missing else 0)"
if errorlevel 2 (
  pause
  exit /b 2
)

%PY% app.py
if errorlevel 1 (
  echo.
  echo [错误] 程序异常退出。请查看 logs\run.log。
  pause
)
