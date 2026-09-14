# ============================================================
# download-mingw.ps1 — 在能上网的电脑上运行：
# 自动下载 winlibs MinGW-w64 (x86_64 posix-seh, 纯 g++) 的 zip，
# 保存为 本目录\tools\mingw.zip，方便上传到无外网的服务器使用。
# 用法：双击 download-mingw.bat，或运行：
#   powershell -ExecutionPolicy Bypass -File download-mingw.ps1
# ============================================================
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$tools = Join-Path $root "tools"
New-Item -ItemType Directory -Path $tools -Force | Out-Null
$out = Join-Path $tools "mingw.zip"

Write-Host "正在从 GitHub 查询 winlibs 最新版..." -ForegroundColor Cyan
$asset = $null
try {
    $api = Invoke-RestMethod -Uri "https://api.github.com/repos/brechtsanders/winlibs_mingw/releases/latest" `
            -Headers @{ "User-Agent"="Mozilla/5.0" } -TimeoutSec 60
    $cands = $api.assets | Where-Object {
        $_.name -like "winlibs-x86_64-posix-seh-gcc-*.zip" -and $_.name -notmatch "llvm|clang"
    }
    $asset = $cands | Sort-Object name | Select-Object -First 1
} catch {
    Write-Host "查询失败：$_" -ForegroundColor Red
}
if (-not $asset) {
    Write-Host "未能自动匹配到 winlibs 的 zip。请手动去以下页面下载：" -ForegroundColor Yellow
    Write-Host "  https://winlibs.com"
    Write-Host "  选 UCRT + POSIX + 64 位的 zip，下载后命名为 tools\mingw.zip"
    exit 1
}

Write-Host "匹配到: $($asset.name)" -ForegroundColor Green
Write-Host "大小约: $([math]::Round($asset.size/1MB,1)) MB" -ForegroundColor Cyan

# 用 EscapeDataString 做 URL 路径段编码，用于国内镜像路径
function UrlEnc([string]$s){ return [System.Uri]::EscapeDataString($s) }

# 候选下载源：先 GitHub，再兰州大学 github-release 国内镜像（中文环境更稳）
$ghUrl = $asset.browser_download_url
$lzuFolder = "https://mirror4.lzu.edu.cn/github-release/brechtsanders/winlibs_mingw/" + (UrlEnc $api.name)
$lzuUrl = "$lzuFolder/$($asset.name)"
$urls = @($ghUrl, $lzuUrl)

$ok = $false
foreach ($u in $urls) {
    Write-Host "尝试下载源: $u" -ForegroundColor DarkGray
    $got = $false
    try {
        Write-Host "  使用 PowerShell 下载中...（文件较大，请耐心，可能数分钟）" -ForegroundColor Cyan
        Invoke-WebRequest -Uri $u -OutFile $out -UseBasicParsing -TimeoutSec 1200 `
            -Headers @{ "User-Agent"="Mozilla/5.0" }
        $got = $true
    } catch {
        Write-Host "  PowerShell 下载失败：$($_.Exception.Message)" -ForegroundColor Yellow
    }
    if (-not $got) {
        try {
            Write-Host "  改用 curl 下载中..." -ForegroundColor Cyan
            curl.exe -L --fail --silent --show-error -A "Mozilla/5.0" $u -o $out
            if ($LASTEXITCODE -eq 0) { $got = $true }
        } catch { Write-Host "  curl 下载失败：$($_.Exception.Message)" -ForegroundColor Yellow }
    }
    if ($got -and (Test-Path $out) -and (Get-Item $out).Length -gt 1000000) { $ok = $true; break }
    if (Test-Path $out) { Remove-Item $out -Force -ErrorAction SilentlyContinue }
}

if (-not $ok) {
    Write-Host "GitHub 与国内镜像都下载失败。请手动去以下页面下载：" -ForegroundColor Red
    Write-Host "  https://winlibs.com"
    Write-Host "  选 UCRT + POSIX + 64 位的 zip（约 $([math]::Round($asset.size/1MB,0))MB），"
    Write-Host "  下载后命名为 tools\mingw.zip 放到本目录 tools\ 下。"
    exit 1
}

Write-Host ""
Write-Host "下载完成：$out" -ForegroundColor Green
Write-Host "文件大小：$([math]::Round((Get-Item $out).Length/1MB,1)) MB"
Write-Host "现在请把这个 tools\mingw.zip 上传到服务器对应目录，即可离线部署。" -ForegroundColor Cyan
