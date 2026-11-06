@echo off
chcp 65001 >nul
cd /d %~dp0

if not exist ".venv\Scripts\python.exe" (
  echo [提示] 未检测到虚拟环境，正在创建 Python 3.12 环境...
  if exist "%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe" (
    "%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe" -m venv .venv
  ) else (
    py -3.12 -m venv .venv 2>nul || python -m venv .venv
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\pip.exe" install -r requirements.txt
)

echo ============================================
echo   三国群雄传平台司令台
echo   打开浏览器: http://127.0.0.1:8765
echo   请先登录官方 PC 微信，再点进入司令台
echo ============================================
".venv\Scripts\python.exe" run.py
pause
