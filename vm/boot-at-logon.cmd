@echo off
rem ============================================================================
rem  虚拟机开机自启：拉起微信，等它起来，再拉起适配器。
rem
rem  装法：把【这个文件的副本】放进
rem      %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
rem  这样登录后会自动跑。目录本身请放在一个固定位置，比如 C:\wxbridge\
rem
rem  踩过的坑，别乱改：
rem   1. 启动微信前必须 cd 到它的安装目录。从别的目录启动，微信会以退出码 20
rem      静默退出，什么日志都没有。
rem   2. 不要用 timeout 命令做等待。它在没有控制台的上下文里会直接报错退出，
rem      后面的适配器就起不来了。用 ping 冒充 sleep。
rem   3. 每一步都往 boot.log 写一行，否则出问题时你完全不知道走到哪了。
rem ============================================================================
setlocal

set "WORK=C:\wxbridge"
set "WECHAT_DIR=C:\Program Files\Tencent\Weixin"

rem ---- 找 pythonw.exe（没有控制台窗口，不会抢焦点） ----
set "PYW="
for %%p in (
  "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
  "C:\Python312\pythonw.exe"
  "C:\Python311\pythonw.exe"
) do if not defined PYW if exist %%p set "PYW=%%~p"
if not defined PYW for /f "delims=" %%p in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%p"
if not defined PYW (
  echo [%date% %time%] ERROR pythonw.exe not found >> "%WORK%\boot.log"
  exit /b 1
)

echo [%date% %time%] boot-at-logon start >> "%WORK%\boot.log"

rem ---- 1) 微信 ----
cd /d "%WECHAT_DIR%"
start "" "Weixin.exe"
echo [%date% %time%] wechat launched >> "%WORK%\boot.log"

rem ---- 2) 等它把界面画出来（30 秒，用 ping 当 sleep） ----
ping -n 31 127.0.0.1 >nul

rem ---- 3) 适配器 ----
cd /d "%WORK%"
start "" "%PYW%" "%WORK%\adapter.py"
echo [%date% %time%] adapter launched >> "%WORK%\boot.log"
