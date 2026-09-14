@echo off
rem ============================================================
rem download-mingw.bat - run this on ANY computer WITH internet
rem to fetch a winlibs MinGW zip and save it as tools\mingw.zip
rem Then upload that one zip to the offline server at:
rem   server_rank\tools\mingw.zip
rem ============================================================
chcp 65001 >nul
echo ================================================================
echo   Download winlibs MinGW -^> tools\mingw.zip
echo   Run this on a computer WITH internet.
echo   Then upload the produced tools\mingw.zip to the offline
echo   server folder: server_rank\tools\mingw.zip
echo ================================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0download-mingw.ps1"
echo.
pause
