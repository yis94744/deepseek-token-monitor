@echo off
chcp 65001 >nul
rem 在 Windows 启动文件夹创建快捷方式，实现开机自启（优先指向 exe）
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK=%STARTUP%\DeepSeekTokenMonitor.lnk"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$target=if(Test-Path '%~dp0DeepSeekTokenMonitor.exe'){'%~dp0DeepSeekTokenMonitor.exe'}else{'%~dp0start.bat'}; $ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut('%LINK%'); $s.TargetPath=$target; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
if exist "%LINK%" (
    echo 开机自启已安装：%LINK%
) else (
    echo 安装失败，请检查权限后重试。
)
pause