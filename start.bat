@echo off
chcp 65001 >nul
rem 启动水豚噜噜监控：优先 exe，否则用 Python 脚本
cd /d "%~dp0"
if exist "%~dp0DeepSeekTokenMonitor.exe" (
    start "" "%~dp0DeepSeekTokenMonitor.exe"
    exit /b 0
)
set "PYW="
for /f "delims=" %%i in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%i"
if defined PYW (
    start "" "%PYW%" "%~dp0token_monitor.py"
    exit /b 0
)
echo 未找到可运行环境：请运行 DeepSeekTokenMonitor.exe，或安装 Python。
pause