@echo off
rem ============================================================
rem build.bat - compile this C++ server with MinGW g++ (ASCII)
rem Dependencies:
rem   1) MinGW-w64 (g++ on PATH)
rem   2) MySQL Connector/C (mysql.h + libmysql)
rem   3) vendor third-party headers (run fetch_deps.ps1 once)
rem MySQL include/lib dirs can be overridden via env:
rem   set MYSQL_INCLUDE=C:\path\include
rem   set MYSQL_LIB=C:\path\lib
rem ============================================================
setlocal

set "ROOT=%~dp0"
set "SRC=%ROOT%src"
set "OUT=%ROOT%build"
set "BIN=%ROOT%token_rank_server.exe"

rem ---------- 1. check g++ ----------
where g++ >nul 2>&1
if errorlevel 1 (
    echo [ERROR] g++ not found. Install MinGW-w64 and add bin to PATH.
    exit /b 1
)

rem ---------- 2. vendor headers ----------
if not exist "%ROOT%vendor\httplib.h" (
    echo [INFO] vendor\httplib.h missing, downloading deps...
    powershell -ExecutionPolicy Bypass -File "%ROOT%fetch_deps.ps1"
)
if not exist "%ROOT%vendor\nlohmann\json.hpp" (
    echo [ERROR] vendor\nlohmann\json.hpp missing. Run: powershell -ExecutionPolicy Bypass -File fetch_deps.ps1
    exit /b 1
)

rem ---------- 3. locate MySQL include/lib ----------
if not defined MYSQL_INCLUDE set "MYSQL_INCLUDE=%ROOT%mysql\include"
if not defined MYSQL_LIB set "MYSQL_LIB=%ROOT%mysql\lib"
if not exist "%MYSQL_INCLUDE%\mysql.h" set "MYSQL_INCLUDE=C:\mysql-connector\include"
if not exist "%MYSQL_INCLUDE%\mysql.h" set "MYSQL_INCLUDE=C:\Program Files\MySQL\MySQL Server 8.0\include"
if not exist "%MYSQL_INCLUDE%\mysql.h" set "MYSQL_INCLUDE=C:\mysql\include"
if not exist "%MYSQL_INCLUDE%\mysql.h" set "MYSQL_INCLUDE=%ROOT%tools\mysql\include"
if not exist "%MYSQL_INCLUDE%\mysql.h" (
    echo [ERROR] mysql.h not found. Install MySQL Connector/C and set MYSQL_INCLUDE.
    echo         Example: set MYSQL_INCLUDE=C:\path\to\mysql\include
    exit /b 1
)

rem ---------- 4. compile ----------
if not exist "%OUT%" mkdir "%OUT%"
echo Compiling... include: %MYSQL_INCLUDE%

g++ -std=c++17 -O2 -Wall -Wextra -c ^
    -I"%ROOT%vendor" -I"%ROOT%src" -I"%MYSQL_INCLUDE%" ^
    "%SRC%\server.cpp" -o "%OUT%\server.o"
if errorlevel 1 ( echo [ERROR] compile server.cpp failed & exit /b 1 )

g++ -std=c++17 -O2 -Wall -Wextra -c ^
    -I"%ROOT%vendor" -I"%ROOT%src" -I"%MYSQL_INCLUDE%" ^
    "%SRC%\db.cpp" -o "%OUT%\db.o"
if errorlevel 1 ( echo [ERROR] compile db.cpp failed & exit /b 1 )

g++ -std=c++17 -O2 -Wall -Wextra -c ^
    -I"%ROOT%vendor" -I"%ROOT%src" -I"%MYSQL_INCLUDE%" ^
    "%SRC%\util.cpp" -o "%OUT%\util.o"
if errorlevel 1 ( echo [ERROR] compile util.cpp failed & exit /b 1 )

echo Linking...
rem Try 1: MinGW import lib (libmysql.a / libmysql.dll.a)
g++ -o "%BIN%" "%OUT%\server.o" "%OUT%\db.o" "%OUT%\util.o" -L"%MYSQL_LIB%" -lmysql -lws2_32
if errorlevel 1 (
    echo   [i] no usable -lmysql import lib; trying to link libmysql.dll directly ...
    set "DLLPATH="
    if exist "%MYSQL_LIB%\libmysql.dll" set "DLLPATH=%MYSQL_LIB%\libmysql.dll"
    if not defined DLLPATH if exist "%MYSQL_BIN%\libmysql.dll" set "DLLPATH=%MYSQL_BIN%\libmysql.dll"
    if not defined DLLPATH if exist "%ROOT%tools\mysql\bin\libmysql.dll" set "DLLPATH=%ROOT%tools\mysql\bin\libmysql.dll"
    if not defined DLLPATH if exist "%ROOT%tools\mysql\lib\libmysql.dll" set "DLLPATH=%ROOT%tools\mysql\lib\libmysql.dll"
    if defined DLLPATH (
        g++ -o "%BIN%" "%OUT%\server.o" "%OUT%\db.o" "%OUT%\util.o" "%DLLPATH%" -lws2_32
        if errorlevel 1 (
            echo [ERROR] direct DLL link also failed.
            echo         Set MYSQL_LIB to the dir with libmysql; if only libmysql.dll exists,
            echo         also set MYSQL_BIN to the dir containing libmysql.dll.
            exit /b 1
        )
    ) else (
        echo [ERROR] link failed and libmysql.dll not found.
        echo         Set MYSQL_LIB to the MySQL Connector lib dir (or MYSQL_BIN to its bin dir).
        exit /b 1
    )
)

echo.
echo [OK] built: %BIN%
echo Run: double-click start.bat  (or run %BIN%)
endlocal
