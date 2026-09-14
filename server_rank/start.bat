@echo off
rem ============================================================
rem start.bat - one-click start (ASCII only)
rem   1) if no config.json, copy from config.example.json
rem   2) if no exe, auto-call build.bat
rem   3) run token_rank_server.exe
rem ============================================================
setlocal
set "ROOT=%~dp0"
set "BIN=%ROOT%token_rank_server.exe"

rem ---------- 1. config ----------
if not exist "%ROOT%config.json" (
    if exist "%ROOT%config.example.json" (
        copy /y "%ROOT%config.example.json" "%ROOT%config.json" >nul
        echo [INFO] config.json generated. Edit it and fill your MySQL password/port.
        echo        Opening in Notepad now...
        notepad "%ROOT%config.json"
    ) else (
        echo [ERROR] config.example.json missing.
        exit /b 1
    )
)

rem ---------- 2. build ----------
if not exist "%BIN%" (
    echo [INFO] exe not found, building...
    call "%ROOT%build.bat"
    if errorlevel 1 exit /b 1
)

rem ---------- 3. run ----------
echo.
echo Starting token_rank_server... (Ctrl+C to stop; log shown in this window)
"%BIN%" "%ROOT%config.json"
if errorlevel 1 (
    echo [ERROR] start failed. Check config.json and that MySQL is running.
    pause
)
endlocal
