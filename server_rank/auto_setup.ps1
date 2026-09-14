# ============================================================================
# auto_setup.ps1  —— 一键自动化：装工具 → 下依赖 → 建库 → 生成配置 → 编译 → 启动
#
# 使用：在 server_rank 目录下双击「一键部署.bat」，或在 PowerShell 里运行：
#     powershell -ExecutionPolicy Bypass -File auto_setup.ps1
#
# 本脚本自动完成：
#   1) 定位/下载 MinGW-w64 (g++)           —— 放到本目录 tools\mingw64
#   2) 定位/下载 MySQL Connector/C          —— 放到本目录 tools\mysql（无需系统安装）
#   3) 下载 cpp-httplib + nlohmann/json     —— 放到 vendor\
#   4) 若本机有 mysql.exe，自动创建库并导入 sql/schema.sql
#   5) 生成 config.json（自动探测，也可按提示覆盖）
#   6) 一键编译 token_rank_server.exe
#   7) 启动服务器
#
# 本机 MySQL 服务账号密码未知时，仅第 4 步需要你输入一次 root 密码（可跳过）。
# ============================================================================
param(
    [switch]$SkipDb,        # 跳过建库(第4步)
    [switch]$OnlyBuild,     # 只到编译完，不自动启动
    [switch]$NoMysqlTool,   # 不下载 MySQL Connector(仅编译时需,已有则别传)
    [switch]$DownloadMinGW  # 允许自动联网下载 MinGW(默认关,避免下载大文件卡死)
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# ---------- 打印 ----------
function Say($m) { Write-Host $m -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [..] $m" -ForegroundColor Yellow }
function Err($m) { Write-Host "  [!!] $m" -ForegroundColor Red }
function Step($m){ Write-Host ""; Write-Host "==== $m ====" -ForegroundColor Yellow }

# ---------- 下载(带备用方式) ----------
function Get-FileRobust([string]$url, [string]$dest) {
    $ok = $false
    try { Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 300; $ok = $true }
    catch { $ok = $false }
    if (-not $ok) {
        try {
            curl.exe -L --fail --silent --show-error $url -o $dest
            if ($LASTEXITCODE -eq 0) { $ok = $true } else { $ok = $false }
        } catch { $ok = $false }
    }
    if (-not $ok) { throw "下载失败: $url" }
}

function Expand-Zip([string]$zip, [string]$destDir) {
    if (Get-Command Expand-Archive -ErrorAction SilentlyContinue) {
        Expand-Archive -Path $zip -DestinationPath $destDir -Force
    } else {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $destDir)
    }
}

# ============================================================================
Step "检查并定位工具链"

# ---- 辅助：从 PATH 或常见目录找含 bin\g++.exe 的 MinGW 根目录 ----
function Find-MingwRoot {
    $cmd = Get-Command g++ -ErrorAction SilentlyContinue
    if ($cmd) { $root1 = Split-Path (Split-Path $cmd.Source); if (Test-Path "$root1\bin\g++.exe") { return $root1 } }
    foreach ($p in @("$env:ProgramFiles\mingw64","C:\mingw64","C:\msys64\mingw64","C:\tools\mingw64",
                     "$root\tools\mingw64","$env:LOCALAPPDATA\Programs\mingw64","C:\Program Files\mingw64")) {
        if (Test-Path "$p\bin\g++.exe") { return $p }
    }
    return $null
}

# ---- 辅助：解压任意 MinGW zip 到 tools\mingw64，返回含 bin\g++.exe 的根目录 ----
function Install-MingwZip([string]$zip) {
    $mingwDir = Join-Path $root "tools\mingw64"
    New-Item -ItemType Directory -Path $mingwDir -Force | Out-Null
    Expand-Zip $zip $mingwDir
    # winlibs zip 顶层通常含 mingw64\ 子目录（或同层直接含 bin\），找到含 bin\g++.exe 的那层上移
    foreach ($round in 0..2) {
        if (Test-Path "$mingwDir\bin\g++.exe") { return $mingwDir }
        $inner = Get-ChildItem $mingwDir -Directory | Where-Object {
            Test-Path (Join-Path $_.FullName "bin\g++.exe")
        } | Select-Object -First 1
        if ($inner) {
            Move-Item -Path (Join-Path $inner.FullName "*") -Destination $mingwDir -Force
            Remove-Item $inner.FullName -Recurse -Force
            continue
        }
        break
    }
    if (Test-Path "$mingwDir\bin\g++.exe") { return $mingwDir }
    return $null
}

# ---- MinGW g++（离线优先，避免联网下载大文件卡死） ----
$gppRoot = Find-MingwRoot
if (-not $gppRoot) {
    $tools = Join-Path $root "tools"; New-Item -ItemType Directory -Path $tools -Force | Out-Null

    # 1) 优先 winget（小/快，装了最好）
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Warn "通过 winget 尝试安装 MinGW ..."
        try {
            winget install --id BrechtSanders.WinLibs.GCC.UCRT --accept-package-agreements --accept-source-agreements --silent | Out-Null
        } catch { Warn "winget 未安装成功：$_" }
        $env:Path = "$env:ProgramFiles\mingw64\bin;$env:Path"
        $gppRoot = Find-MingwRoot
    }

    # 2) 离线包 tools\mingw.zip（首选，不联网）
    if (-not $gppRoot) {
        $zipOffline = Join-Path $tools "mingw.zip"
        if (Test-Path $zipOffline) {
            Warn "找到离线包 tools\mingw.zip，正在解压..."
            $gppRoot = Install-MingwZip $zipOffline
            Remove-Item $zipOffline -Force -ErrorAction SilentlyContinue
            if ($gppRoot) { Ok "已从离线包装好 MinGW" }
        } else {
            Warn "未找到离线包 tools\mingw.zip"
        }
    }

    # 3) 可选自动联网下载（默认关闭，避免卡死；用 -DownloadMinGW 开启）
    if (-not $gppRoot -and $DownloadMinGW) {
        Warn "尝试联网下载 winlibs MinGW（下载大文件，可能较慢/失败）..."
        $zipDl = Join-Path $tools "mingw-dl.zip"
        try {
            $api = Invoke-RestMethod -Uri "https://api.github.com/repos/brechtsanders/winlibs_mingw/releases/latest" `
                    -Headers @{ "User-Agent"="Mozilla/5.0" } -TimeoutSec 60
            $assets = $api.assets | Where-Object {
                $_.name -like "winlibs-x86_64-posix-seh-gcc-*.zip" -and $_.name -notmatch "llvm|clang"
            }
            $asset = $assets | Sort-Object name | Select-Object -First 1
            if ($asset) {
                Warn "选中下载: $($asset.name)"
                Get-FileRobust $asset.browser_download_url $zipDl
                if ((Get-Item $zipDl).Length -gt 1000000) {
                    $gppRoot = Install-MingwZip $zipDl
                    Remove-Item $zipDl -Force -ErrorAction SilentlyContinue
                }
            }
        } catch { Warn "联网下载 MinGW 失败：$_"; Remove-Item $zipDl -Force -ErrorAction SilentlyContinue }
    }
}

if (-not $gppRoot) {
    Err "未找到 g++。为避免联网下载大文件卡住，请："
    Err "   1) 在能上网的机器下载一个 MinGW-w64 的 zip（x86_64，如 winlibs）"
    Err "      → https://winlibs.com  或  https://github.com/brechtsanders/winlibs_mingw/releases"
    Err "   2) 把该 zip 上传到本目录，命名为 tools\mingw.zip"
    Err "   3) 重跑本脚本（会自动解压 tools\mingw.zip）"
    Err "或者：直接把已装 MinGW 的 bin 加入系统 PATH。"
    Err "（若你确认网络能下大文件，可加 -DownloadMinGW 参数让脚本自动联网下载）"
    exit 1
}

# 把 MinGW bin 加入当前进程 PATH（不影响系统）
if (Test-Path "$gppRoot\bin") { $env:Path = "$gppRoot\bin;$env:Path" }
$gppTest = Get-Command g++ -ErrorAction SilentlyContinue
if (-not $gppTest) { Err "仍找不到 g++：目录 $gppRoot 里没有可用的 g++.exe。"; exit 1 }
Ok("使用 g++: $($gppTest.Source)")

# ============================================================================
Step "下载第三方头文件 (cpp-httplib / nlohmann-json)"
& powershell -ExecutionPolicy Bypass -File (Join-Path $root "fetch_deps.ps1") | Out-Null
if (-not (Test-Path (Join-Path $root "vendor\httplib.h")) -or
    -not (Test-Path (Join-Path $root "vendor\nlohmann\json.hpp"))) {
    Err "第三方头文件下载失败，请检查网络后重跑 fetch_deps.ps1"
    exit 1
}
Ok "第三方头文件就绪"

# ============================================================================
Step "定位 MySQL Connector/C (mysql.h + libmysql)"
if (-not $NoMysqlTool) {
    $foundInc = $null
    foreach ($p in @("$root\mysql\include", "$root\tools\mysql\include", "$env:ProgramFiles\MySQL\MySQL Connector C 8.0\include",
                     "C:\Program Files\MySQL\MySQL Server 8.0\include", "$env:ProgramFiles\MySQL\MySQL Server 8.0\include",
                     "$root\mysql-connector\include")) {
        if (Test-Path "$p\mysql.h") { $foundInc = $p; break }
    }
    if (-not $foundInc) {
        $tools = Join-Path $root "tools"; New-Item -ItemType Directory -Path $tools -Force | Out-Null
        $zip = Join-Path $tools "mysql-connector.zip"
        $got = $false

        # 1) 离线包优先：tools\mysql-connector.zip（不联网）
        if (-not $got -and (Test-Path $zip)) {
            Warn "使用离线包 tools\mysql-connector.zip ..."
            $got = $true
        }

        # 2) 否则尝试自动联网下载（cdn.mysql.com 可达但文件名要匹配；网关 /get/ 需浏览器头）
        if (-not $got) {
            Warn "未找到离线包，尝试联网下载 MySQL Connector/C 到 tools\mysql ..."
            # 服务器实测 cdn.mysql.com/Downloads/... 可达(404=文件名不对)，故列举多个真实版本文件名
            $ver = @("8.0.37","8.0.36","8.0.33","8.0.32","8.0.31","8.0.30","8.0.29","8.4.0","9.2.0","9.0.1")
            $hosts = @("https://cdn.mysql.com/Downloads/Connector-C",
                       "https://ftp.kaist.ac.kr/mysql/Downloads/Connector-C")
            $cand = @()
            foreach ($h in $hosts) { foreach ($v in $ver) { $cand += "$h/mysql-connector-c-$v-winx64.zip" } }
            foreach ($u in $cand) {
                Warn "尝试: $u"
                $okThis = $false
                if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                    curl.exe -L --fail --silent --show-error --connect-timeout 25 --max-time 200 `
                        -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36" `
                        -e "https://dev.mysql.com/downloads/connector/c/" -o $zip $u 2>$null
                    if ($LASTEXITCODE -eq 0 -and (Test-Path $zip) -and (Get-Item $zip).Length -gt 100000) { $okThis = $true }
                }
                if (-not $okThis) {
                    try {
                        Invoke-WebRequest -Uri $u -OutFile $zip -UseBasicParsing -TimeoutSec 90 -MaximumRedirection 10 `
                            -Headers @{ "User-Agent"="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36" }
                        if ((Test-Path $zip) -and (Get-Item $zip).Length -gt 100000) { $okThis = $true }
                    } catch { }
                }
                if ($okThis) { $got = $true; break }
                if (Test-Path $zip) { Remove-Item $zip -Force -ErrorAction SilentlyContinue }
            }
        }

        if (-not $got) {
            Err "没有可用的 MySQL Connector/C（离线包缺失且自动下载失败）。"
            Err "请在能上网的机器下载任一 mysql-connector-c-*-winx64.zip"
            Err "（https://dev.mysql.com/downloads/connector/c/ ），"
            Err "上传到本目录命名为 tools\mysql-connector.zip，再重跑本脚本。"
            exit 1
        }
        $destMysql = Join-Path $tools "mysql"
        New-Item -ItemType Directory -Path $destMysql -Force | Out-Null
        Expand-Zip $zip $destMysql
        $inner = Get-ChildItem $destMysql -Directory | Select-Object -First 1
        if ($inner) { Move-Item -Path (Join-Path $inner.FullName "*") -Destination $destMysql -Force; Remove-Item $inner.FullName -Recurse -Force }
        $foundInc = Join-Path $destMysql "include"
    }
    Ok "MySQL 头文件目录: $foundInc"
    $mysqlRoot = Split-Path $foundInc
    $mysqlLibDir = Join-Path $mysqlRoot "lib"
    if (-not (Test-Path "$mysqlLibDir\libmysql.lib") -and -not (Test-Path "$mysqlLibDir\libmysql.a")) {
        # 兼容其它布局
        $sub = Get-ChildItem $mysqlRoot -Recurse -Filter "libmysql.lib" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($sub) { $mysqlLibDir = Split-Path $sub.FullName }
    }
    # 定位含 libmysql.dll 的 bin 目录（直连 DLL 编译用）
    $dll = Get-ChildItem $mysqlRoot -Recurse -Filter "libmysql.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($dll) { $env:MYSQL_BIN = Split-Path $dll.FullName }
    $env:MYSQL_INCLUDE = $foundInc
    $env:MYSQL_LIB = $mysqlLibDir
    Ok "MySQL lib 目录: $mysqlLibDir"
    if ($env:MYSQL_BIN) { Ok "MySQL bin 目录(含 libmysql.dll): $env:MYSQL_BIN" }
}

# ============================================================================
Step "建库 & 导入 schema.sql（可跳过）"
if (-not $SkipDb) {
    $mysqlCli = Get-Command mysql -ErrorAction SilentlyContinue
    if ($mysqlCli) {
        try {
            $pw = Read-Host "  请输入本机 MySQL root 密码（建库用；跳过直接回车）"
            if ($pw) {
                $env:MYSQL_PWD = $pw
                # 建库建表（schema.sql 内含 CREATE DATABASE/USE）——用 SOURCE 执行，避免 stdin 重定向
                $schema = (Join-Path $root "sql\schema.sql").Replace('\','/')
                & $mysqlCli.Source -uroot --execute="source $schema"
                if ($LASTEXITCODE -eq 0) { Ok "数据库 token_rank 已创建并导入表结构" }
                else { Warn "schema 导入失败（可能密码/权限不对，或已建过）。可手动导入 sql/schema.sql" }
                Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
            } else {
                Warn "已跳过建库。请稍后手动导入 sql\schema.sql。"
            }
        } catch { Warn "建库失败被忽略：$_" }
    } else {
        Warn "本机未在 PATH 找到 mysql.exe，跳过自动建库。请手动导入 sql/schema.sql。"
    }
}

# ============================================================================
Step "生成 config.json（若不存在）"
$cfg = Join-Path $root "config.json"
if (-not (Test-Path $cfg)) {
    Copy-Item (Join-Path $root "config.example.json") $cfg -Force
    # 从环境读默认
    $dbHost = Read-Host "  数据库地址(默认127.0.0.1)"
    if (-not $dbHost) { $dbHost = "127.0.0.1" }
    $dbUser = Read-Host "  数据库用户(默认root)"
    if (-not $dbUser) { $dbUser = "root" }
    $dbPwd  = Read-Host "  数据库密码(默认空)"
    $dbName = "token_rank"
    $port   = Read-Host "  服务端口(默认8899)"
    if (-not $port) { $port = "8899" }
    $obj = @{ server = @{ host="0.0.0.0"; port=[int]$port; session_max_age_days=30 }
              mysql  = @{ host=$dbHost; port=3306; user=$dbUser; password=$dbPwd; database=$dbName; charset="utf8mb4" } }
    # 用无 BOM 的 UTF-8 写，避免 nlohmann(json) 解析带 BOM 的文件报错
    $jsonText = $obj | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($cfg, $jsonText, (New-Object System.Text.UTF8Encoding($false)))
    Ok "已生成 $cfg"
} else {
    Ok "config.json 已存在，沿用当前配置"
}

# ============================================================================
Step "一键编译"
& (Join-Path $root "build.bat")
if ($LASTEXITCODE -ne 0) { Err "编译失败，见上方报错"; exit 1 }
Ok "编译完成：$root\token_rank_server.exe"

if ($OnlyBuild) {
    Say "仅编译模式结束。可手动运行：token_rank_server.exe config.json"
    exit 0
}

# ============================================================================
Step "启动服务器"
$bin = Join-Path $root "token_rank_server.exe"
# 确保 libmysql.dll 与 exe 同目录（在下载的 tools\mysql\bin 或 lib 里）
foreach ($dir in @($env:MYSQL_BIN, $env:MYSQL_LIB)) {
    if (-not $dir -or -not (Test-Path $dir)) { continue }
    foreach ($dll in @("libmysql.dll","libmariadb.dll")) {
        $src = Get-ChildItem -Path $dir -Filter $dll -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($src -and -not (Test-Path (Join-Path $root $dll))) {
            Copy-Item $src.FullName (Join-Path $root $dll) -Force
            Ok "复制 $dll 到运行目录"
        }
    }
}
Say "正在启动 $bin (Ctrl+C 停止)..."
& $bin (Join-Path $root "config.json")
