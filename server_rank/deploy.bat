@echo off
chcp 65001 >nul
rem ASCII-only launcher for auto_setup.ps1
rem Double-click this, or run:  deploy.bat
rem Options:
rem   -SkipDb     skip DB creation
rem   -OnlyBuild  build only, do not start
echo ================================================================
echo   Token Rank Server - one-click deploy / start
echo ================================================================
echo.
choice /c YN /m "Start now? [Y=Yes N=No]"
if errorlevel 2 exit /b 0

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0auto_setup.ps1" %*
if errorlevel 1 (
    echo.
    echo [FAILED] Copy the error above and report it.
    pause
)
endlocal
