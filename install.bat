@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================
echo   三国群雄传平台司令台 - 一键装机
echo ============================================
echo.

set "PY312="
set "ERR=0"

call :find_python312
if defined PY312 goto :have_python

echo [1/4] 未检测到 Python 3.12，尝试用 winget 安装...
where winget >nul 2>&1
if errorlevel 1 (
  echo [失败] 未找到 winget，请手动安装 Python 3.12：
  echo   https://www.python.org/downloads/release/python-31210/
  echo   安装时勾选 Add python.exe to PATH
  set "ERR=1"
  goto :summary
)

winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo [失败] winget 安装 Python 3.12 失败，请手动安装后重试。
  set "ERR=1"
  goto :summary
)

rem 新装后当前窗口 PATH 可能未刷新，优先用默认安装路径
set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%LocalAppData%\Programs\Python\Launcher;%PATH%"
call :find_python312
if not defined PY312 (
  echo [失败] Python 3.12 已安装，但当前窗口仍找不到。
  echo   请关闭本窗口，重新双击 install.bat。
  set "ERR=1"
  goto :summary
)

:have_python
echo [1/4] Python 3.12 就绪：%PY312%
for /f "delims=" %%V in ('"%PY312%" --version 2^>nul') do echo         %%V

echo [2/4] 创建 / 复用虚拟环境 .venv ...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" 2>nul
  if errorlevel 1 (
    echo         已有 .venv 但不是 3.12，正在重建...
    rmdir /s /q ".venv" 2>nul
  )
)
if not exist ".venv\Scripts\python.exe" (
  "%PY312%" -m venv .venv
  if errorlevel 1 (
    echo [失败] 创建虚拟环境失败
    set "ERR=1"
    goto :summary
  )
)

echo [3/4] 安装 Python 依赖 requirements.txt ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo [失败] 升级 pip 失败
  set "ERR=1"
  goto :summary
)
".venv\Scripts\pip.exe" install -r requirements.txt
if errorlevel 1 (
  echo [失败] 安装依赖失败
  set "ERR=1"
  goto :summary
)

echo [4/4] 检查官方 PC 微信...
set "WX_OK=0"
tasklist /FI "IMAGENAME eq Weixin.exe" 2>nul | find /I "Weixin.exe" >nul && set "WX_OK=1"
tasklist /FI "IMAGENAME eq WeChat.exe" 2>nul | find /I "WeChat.exe" >nul && set "WX_OK=1"
if "%WX_OK%"=="1" (
  echo         已检测到微信进程。
) else (
  echo         [提示] 未检测到微信进程。
  echo         请安装并登录「微信电脑版」官方客户端后再启动司令台。
  where winget >nul 2>&1
  if not errorlevel 1 (
    echo         可选：winget install -e --id Tencent.WeChat
  )
)

:summary
echo.
echo ============================================
if "%ERR%"=="0" (
  echo   装机完成
  echo ============================================
  echo.
  echo 【换机后仍须人工确认】
  echo   1. 官方 PC 微信已登录，主窗口可打开（勿只留托盘）
  echo   2. 双击 start.bat 启动
  echo   3. 浏览器打开 http://127.0.0.1:8765
  echo   4. 填写群名（须与微信群名完全一致）
  echo   5. 先开「演练模式」试发，再关演练正式播报
  echo   6. 试发时建议先关掉浏览器开发者工具
  echo.
  echo 下一步：可直接运行 start.bat
) else (
  echo   装机未完成，请按上方提示处理后重试
  echo ============================================
)
echo.
pause
exit /b %ERR%

:find_python312
set "PY312="
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  set "PY312=%LocalAppData%\Programs\Python\Python312\python.exe"
  goto :eof
)
if exist "%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe" (
  set "PY312=%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe"
  goto :eof
)
where py >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "PY312=%%P"
  if defined PY312 goto :eof
)
where python >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%P in ('python -c "import sys; print(sys.executable if sys.version_info[:2]==(3,12) else '')" 2^>nul') do set "PY312=%%P"
)
goto :eof
