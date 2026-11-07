@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

rem 未装机或依赖缺失时，先走一键装机
set "NEED_INSTALL=0"
if not exist ".venv\Scripts\python.exe" set "NEED_INSTALL=1"
if "%NEED_INSTALL%"=="0" (
  ".venv\Scripts\python.exe" -c "import fastapi,uvicorn,apscheduler,uiautomation,pyperclip,win32gui" 2>nul
  if errorlevel 1 set "NEED_INSTALL=1"
)

if "%NEED_INSTALL%"=="1" (
  echo [提示] 环境未就绪，先执行一键装机...
  echo.
  call "%~dp0install.bat"
  if errorlevel 1 (
    echo [失败] 装机未完成，无法启动。
    pause
    exit /b 1
  )
  if not exist ".venv\Scripts\python.exe" (
    echo [失败] 装机后仍无虚拟环境。
    pause
    exit /b 1
  )
)

echo ============================================
echo   三国群雄传平台司令台
echo   打开浏览器: http://127.0.0.1:8765
echo   请先登录官方 PC 微信（主窗口可打开），再点进入司令台
echo ============================================
".venv\Scripts\python.exe" run.py
pause
