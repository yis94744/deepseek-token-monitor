@echo off
chcp 65001 >nul
rem ============================================================
rem One-click deploy launcher (ASCII only to avoid codepage bugs)
rem Usage: double-click this file. Optional args:
rem   -SkipDb     skip DB creation
rem   -OnlyBuild  build only, do not start
rem ============================================================
setlocal
title Token Rank Server - One Click Deploy
echo ================================================================
echo   Token Rank Server  --  one-click deploy / start
echo   (Chinese messages will appear below in auto_setup.ps1)
echo ================================================================
echo.
choice /c YN /m "Start now? [Y=Yes N=No]"
if errorlevel 2 exit /b 0

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0auto_setup.ps1" %*
if errorlevel 1 (
    echo.
    echo [FAILED] Please copy the error above and report it.
    pause
)
endlocal
