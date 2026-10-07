@echo off
rem 单独把适配器拉起来（调试用；正常情况是 boot-at-logon.cmd 负责）
rem 用 pythonw.exe：不带控制台窗口，不会抢焦点
setlocal
set "WORK=C:\wxbridge"

set "PYW="
for %%p in (
  "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
  "C:\Python312\pythonw.exe"
) do if not defined PYW if exist %%p set "PYW=%%~p"
if not defined PYW for /f "delims=" %%p in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%p"
if not defined PYW (
  echo 找不到 pythonw.exe
  exit /b 1
)

cd /d "%WORK%"
start "" "%PYW%" "%WORK%\adapter.py"
