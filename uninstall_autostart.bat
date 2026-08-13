@echo off
chcp 65001 >nul
rem 删除启动文件夹中的快捷方式，取消开机自启
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DeepSeekTokenMonitor.lnk"
if exist "%LINK%" (
    del "%LINK%"
    echo 已删除开机自启快捷方式。
) else (
    echo 未找到开机自启快捷方式。
)
pause