@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"

rem ---- 找 Python：优先用本目录下的 venv，其次 PATH 里的 ----
set "PY="
if exist "%HERE%venv\Scripts\python.exe" set "PY=%HERE%venv\Scripts\python.exe"
if not defined PY for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"
if not defined PY (
  echo [错误] 找不到 Python。
  echo        要么装一个，要么在本目录执行： python -m venv venv
  pause
  exit /b 1
)

if not exist "%HERE%config.json" (
  echo [错误] 没有 config.json。
  echo        把 config.example.json 复制成 config.json 再填好。
  pause
  exit /b 1
)

rem ---- 把临时目录也钉在本目录，别往 C 盘灌 ----
if not exist "%HERE%temp" mkdir "%HERE%temp"
set "TEMP=%HERE%temp"
set "TMP=%HERE%temp"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

cd /d "%HERE%"
echo ============================================================
echo  微信 ^<-^> Agent 桥接
echo  目录   : %HERE%
echo  适配器 : 见 config.json 的 adapter_url
echo  Ctrl+C 停止
echo ============================================================
echo.

"%PY%" -u "%HERE%bridge.py"
echo.
echo [桥接已退出，退出码 %ERRORLEVEL%]
pause
