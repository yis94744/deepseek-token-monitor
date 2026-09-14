# CloudRank service watchdog (ASCII only - avoids codepage issues)
# Called by the scheduled task every 5 minutes and at system startup.
#
# Logic:
#   1. Probe http://127.0.0.1/api/board  -> if it answers (any HTTP code), the
#      service is HEALTHY: do nothing (idempotent, safe to run every 5 min).
#   2. If the probe fails, the service is DEAD OR ZOMBIE (process alive but the
#      listener is gone - exactly the 2026-09-09 failure). Kill any leftover
#      python processes and start a fresh launcher.
#
# Exit codes: 0 = healthy or restarted, 1 = restart failed.

$ErrorActionPreference = 'SilentlyContinue'

$APP_DIR  = 'C:\cloud_rank'
$PY       = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python310-32\python.exe'
$LAUNCHER = Join-Path $APP_DIR 'start_server.py'
$LOG      = Join-Path $APP_DIR 'watchdog.log'

function Write-Log($msg) {
    $line = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $msg
    Add-Content -Path $LOG -Value $line -Encoding UTF8
    # Keep watchdog.log small: rotate at 1 MB, keep 1 old copy.
    $fi = Get-Item $LOG -ErrorAction SilentlyContinue
    if ($fi -and $fi.Length -gt 1MB) {
        Move-Item $LOG ($LOG + '.1') -Force
    }
}

function Test-Service {
    # Any HTTP response (even 401) means the listener is alive.
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1/api/board' -UseBasicParsing -TimeoutSec 8
        return $true
    } catch {
        $resp = $_.Exception.Response
        if ($resp -ne $null) { return $true }   # got a status code -> alive
        return $false
    }
}

function Stop-AllPython {
    $procs = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' })
    foreach ($p in $procs) {
        Write-Log ("killing stale python PID=" + $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($procs.Count -gt 0) { Start-Sleep -Seconds 3 }
    return $procs.Count
}

if (-not (Test-Path $LAUNCHER)) {
    Write-Log ('ERROR launcher missing: ' + $LAUNCHER)
    exit 1
}

if (Test-Service) {
    # Healthy - stay quiet to keep the log readable.
    exit 0
}

Write-Log 'service not responding -> restarting'

# Kill leftovers first: a zombie may still hold the socket, and a fresh bind
# would then fail with Errno 10048 (this happened before).
Stop-AllPython | Out-Null

Start-Process -FilePath $PY -ArgumentList $LAUNCHER -WindowStyle Hidden
Start-Sleep -Seconds 8

if (Test-Service) {
    Write-Log 'restart OK - service is responding'
    exit 0
} else {
    Write-Log 'restart FAILED - service still not responding'
    exit 1
}
