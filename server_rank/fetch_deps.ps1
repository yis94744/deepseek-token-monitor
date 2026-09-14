# ============================================================
# fetch_deps.ps1 — 自动下载第三方头文件到 vendor/ 目录
# 需要网络连接。运行：powershell -ExecutionPolicy Bypass -File fetch_deps.ps1
# 含多个镜像源：raw.githubusercontent.com → jsDelivr(国内可达) → Gitee
# ============================================================
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$vendor = Join-Path $root "vendor"

New-Item -ItemType Directory -Path $vendor -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $vendor "nlohmann") -Force | Out-Null

function Download-Robust([string]$name, [string[]]$urls, [string]$dest) {
    Write-Host "== 下载 $name =="
    foreach ($u in $urls) {
        Write-Host " 尝试: $u"
        try {
            Invoke-WebRequest -Uri $u -OutFile $dest -UseBasicParsing -TimeoutSec 120 `
                -Headers @{ "User-Agent" = "Mozilla/5.0" }
            if ((Get-Item $dest).Length -gt 1000) { Write-Host "  完成: $dest"; return $true }
        } catch {
            Write-Host "  失败: $($_.Exception.Response.StatusCode.value__)"
        }
        # 再试 curl
        try {
            curl.exe -L --fail --silent --show-error -A "Mozilla/5.0" $u -o $dest
            if ($LASTEXITCODE -eq 0 -and (Get-Item $dest).Length -gt 1000) { Write-Host "  完成(curl): $dest"; return $true }
        } catch {}
        if (Test-Path $dest) { Remove-Item $dest -Force -ErrorAction SilentlyContinue }
    }
    Write-Host "  [!!] 所有源都下载 $name 失败"
    return $false
}

# ---------- cpp-httplib ----------
Download-Robust "cpp-httplib (httplib.h)" @(
    "https://raw.githubusercontent.com/yhirose/cpp-httplib/master/httplib.h",
    "https://cdn.jsdelivr.net/gh/yhirose/cpp-httplib@master/httplib.h"
) (Join-Path $vendor "httplib.h")

# ---------- nlohmann/json ----------
Download-Robust "nlohmann-json (json.hpp)" @(
    "https://raw.githubusercontent.com/nlohmann/json/develop/single_include/nlohmann/json.hpp",
    "https://cdn.jsdelivr.net/gh/nlohmann/json@develop/single_include/nlohmann/json.hpp"
) (Join-Path $vendor "nlohmann\json.hpp")

Write-Host ""
if ((Test-Path (Join-Path $vendor "httplib.h")) -and
    (Test-Path (Join-Path $vendor "nlohmann\json.hpp"))) {
    Write-Host "依赖头文件下载完成。httplib 与 nlohmann 均为单头文件，编译时无需额外链接库。"
} else {
    Write-Host "有依赖下载失败，请检查网络/镜像后重试。"
}
