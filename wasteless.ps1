#
# wasteless.ps1 - WasteLess CLI for native Windows (counterpart of wasteless.sh)
#
# Usage (through the wasteless.cmd shim installed by install.ps1):
#   wasteless             Start the web UI in background (default)
#   wasteless stop        Stop the web UI
#   wasteless logs        View server logs (follow)
#   wasteless status      Check if server + auto-collection are running
#   wasteless collect     Run the complete collection and detection pipeline
#   wasteless schedule    Install auto-collection every 5 min (Task Scheduler)
#   wasteless unschedule  Remove the scheduled auto-collection
#
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts
# in the ANSI code page, so any accent would be garbled.
#

param([string]$Command = "start")

$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$HomeDir = $env:USERPROFILE

$PidFile = Join-Path $HomeDir ".wasteless.pid"
$LogFile = Join-Path $HomeDir ".wasteless.log"
$CollectorPidFile = Join-Path $HomeDir ".wasteless-collector.pid"
$CollectLockDir = Join-Path $HomeDir ".wasteless-collect.lock.d"

$CollectIntervalSec = 300
$TaskName = "WasteLessCollect"

$RootPython = Join-Path $ScriptDir "venv\Scripts\python.exe"
$RootPythonW = Join-Path $ScriptDir "venv\Scripts\pythonw.exe"
$UiPython = Join-Path $ScriptDir "ui\venv\Scripts\python.exe"
$Runner = Join-Path $ScriptDir "src\run_collection.py"

# UTF-8 mode for every Python process we start (and their children):
# Windows would otherwise read/write the repo's UTF-8 files as cp1252.
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

function Write-Ok($msg)   { Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $msg }
function Write-Warn($msg) { Write-Host "[WARN] " -ForegroundColor Yellow -NoNewline; Write-Host $msg }
function Write-Err($msg)  { Write-Host "[ERROR] " -ForegroundColor Red -NoNewline; Write-Host $msg }

# KEY=VALUE lookup without dot-sourcing anything (same rule as wasteless.sh:
# a password containing special characters must never be interpreted).
function Get-EnvVar([string]$Key, [string]$File) {
    if (-not (Test-Path $File)) { return $null }
    $line = Get-Content $File | Where-Object { $_ -match "^$([regex]::Escape($Key))=" } | Select-Object -Last 1
    if ($null -eq $line) { return $null }
    return $line.Substring($Key.Length + 1)
}

function Get-LivePid([string]$File) {
    if (-not (Test-Path $File)) { return $null }
    $raw = (Get-Content $File -Raw).Trim()
    $procId = 0
    if (-not [int]::TryParse($raw, [ref]$procId)) { return $null }
    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) { return $procId }
    return $null
}

# Kill a process and its children (uvicorn runs under a cmd.exe wrapper).
# Redirection inside cmd: in Windows PowerShell 5.1, a native command's
# stderr under $ErrorActionPreference=Stop would throw.
function Stop-Tree([int]$ProcId) {
    & cmd.exe /c "taskkill /PID $ProcId /T /F >nul 2>&1"
}

function Get-PortOwner([int]$Port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        return $conn.OwningProcess
    } catch {
        return $null
    }
}

function Test-PortOpen([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        return $client.ConnectAsync("127.0.0.1", $Port).Wait(500)
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Get-UiPort {
    $port = $env:WASTELESS_PORT
    if (-not $port) { $port = Get-EnvVar "STREAMLIT_SERVER_PORT" (Join-Path $ScriptDir "ui\.env") }
    if (-not $port) { $port = "8888" }
    return [int]$port
}

function Test-ScheduledTaskInstalled {
    return $null -ne (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
}

# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------
function Invoke-Start {
    $uiDir = Join-Path $ScriptDir "ui"
    $uiEnv = Join-Path $uiDir ".env"
    if (-not (Test-Path $uiEnv)) {
        Write-Err "ui\.env not found. Run install.cmd first."
        exit 1
    }
    if (-not (Test-Path $UiPython)) {
        Write-Err "Virtual environment not found in ui\. Run install.cmd first."
        exit 1
    }

    $port = Get-UiPort
    # Landing page: always the home page, the server decides whether to
    # redirect to /setup (single source of truth, see wasteless.sh).
    $landingUrl = "http://localhost:$port"

    if (Get-LivePid $PidFile) {
        Write-Host "WasteLess is already running -> $landingUrl" -ForegroundColor Green
        Start-Process $landingUrl
        exit 0
    }

    $owner = Get-PortOwner $port
    if ($owner) {
        $procName = (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName
        Write-Host "Port $port is already in use by '$procName' (PID $owner)." -ForegroundColor Yellow
        Write-Host "To use a different port:  `$env:WASTELESS_PORT=8889; wasteless"
        exit 1
    }

    Start-CollectorLoop

    Write-Host ""
    Write-Host "  Starting WasteLess..." -ForegroundColor Yellow
    Write-Host ""

    # Loopback by default: the API has no authentication and its POST
    # endpoints execute real AWS actions. WASTELESS_HOST=0.0.0.0 exposes it
    # on the network (at your own risk). Env var > ui/.env > 127.0.0.1.
    $bindHost = $env:WASTELESS_HOST
    if (-not $bindHost) { $bindHost = Get-EnvVar "WASTELESS_HOST" $uiEnv }
    if (-not $bindHost) { $bindHost = "127.0.0.1" }

    # cmd.exe wrapper only to append stdout+stderr to the log (Start-Process
    # redirection truncates and needs two files). Hidden window, detached.
    $cmdLine = "/c `"`"$UiPython`" -m uvicorn main:app --host $bindHost --port $port >> `"$LogFile`" 2>&1`""
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdLine -WorkingDirectory $uiDir `
        -WindowStyle Hidden -PassThru
    Set-Content -Path $PidFile -Value $proc.Id -Encoding ascii

    $spin = @('|', '/', '-', '\')
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-PortOpen $port) {
            Write-Host "`r  Ready -> $landingUrl                    " -ForegroundColor Green
            Write-Host ""
            Write-Host "  wasteless logs     View server logs" -ForegroundColor Cyan
            Write-Host "  wasteless stop     Stop the server" -ForegroundColor Cyan
            Write-Host ""
            Start-Process $landingUrl
            return
        }
        if ($proc.HasExited) {
            Write-Host ""
            Write-Err "The server exited during startup. Last log lines ($LogFile):"
            if (Test-Path $LogFile) { Get-Content $LogFile -Tail 20 | ForEach-Object { Write-Host "    $_" } }
            Remove-Item $PidFile -ErrorAction SilentlyContinue
            exit 1
        }
        Write-Host -NoNewline ("`r  {0}  Starting up... ({1}s)" -f $spin[$i % 4], [int]($i / 2))
        Start-Sleep -Milliseconds 500
    }

    Write-Host "`r  Still starting - first run may take a minute         " -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  PID $($proc.Id)"
    Write-Host "  wasteless logs     View server logs" -ForegroundColor Cyan
    Write-Host "  wasteless stop     Stop the server" -ForegroundColor Cyan
    Write-Host ""
}

# In-process fallback loop, only when no scheduled task exists (same rule
# as _ensure_cron in wasteless.sh): it dies with `wasteless stop` and with
# the session, the scheduled task survives reboots.
function Start-CollectorLoop {
    if (Test-ScheduledTaskInstalled) {
        Write-Host "  Auto-collection: scheduled task active (survives reboot)" -ForegroundColor Cyan
        return
    }
    if (Get-LivePid $CollectorPidFile) { return }
    if (-not (Test-Path $RootPythonW)) {
        Write-Warn "venv\Scripts\pythonw.exe not found - auto-collection not started (run install.cmd)"
        return
    }
    $collectorArgs = "`"$Runner`" --log `"$LogFile`" --every $CollectIntervalSec"
    $proc = Start-Process -FilePath $RootPythonW -ArgumentList $collectorArgs -WorkingDirectory $ScriptDir -PassThru
    Set-Content -Path $CollectorPidFile -Value $proc.Id -Encoding ascii
    Write-Host "  Auto-collection started (in-process, every 5 min)" -ForegroundColor Cyan
    Write-Host "  Tip: 'wasteless schedule' for a collection that survives reboot" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------
function Invoke-Stop {
    $collectorPid = Get-LivePid $CollectorPidFile
    if ($collectorPid) { Stop-Tree $collectorPid }
    Remove-Item $CollectorPidFile -ErrorAction SilentlyContinue
    Remove-Item $CollectLockDir -Recurse -Force -ErrorAction SilentlyContinue

    $stopped = $false
    $uiPid = Get-LivePid $PidFile
    if ($uiPid) {
        Stop-Tree $uiPid
        $stopped = $true
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue

    # Safety net: whatever still listens on the UI port (manual start, old PID file)
    $owner = Get-PortOwner (Get-UiPort)
    if ($owner) {
        Stop-Tree $owner
        $stopped = $true
    }

    if ($stopped) { Write-Host "WasteLess stopped" -ForegroundColor Green }
    else { Write-Host "WasteLess is not running" }
}

# ---------------------------------------------------------------------------
# logs / status
# ---------------------------------------------------------------------------
function Invoke-Logs {
    if (Test-Path $LogFile) { Get-Content $LogFile -Tail 50 -Wait }
    else { Write-Host "No log file found at $LogFile" }
}

function Invoke-Status {
    $uiPid = Get-LivePid $PidFile
    if ($uiPid) {
        Write-Host "Running" -ForegroundColor Green -NoNewline
        Write-Host " - PID $uiPid -> http://localhost:$(Get-UiPort)"
    } else {
        Write-Host "Not running"
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    }
    Write-Host ""
    Write-Host "Auto-collection (Task Scheduler)"
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        $info = $task | Get-ScheduledTaskInfo
        Write-Host "  Active" -ForegroundColor Green -NoNewline
        Write-Host " - $TaskName (state: $($task.State), last run: $($info.LastRunTime), next: $($info.NextRunTime))"
    } elseif (Get-LivePid $CollectorPidFile) {
        Write-Host "  In-process loop running (stops with 'wasteless stop'; 'wasteless schedule' to persist)"
    } else {
        Write-Host "  Inactive (run 'wasteless schedule')"
    }
}

# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------
function Invoke-Collect {
    if (-not (Test-Path $RootPython)) {
        Write-Err "Virtual environment not found. Run install.cmd first."
        exit 1
    }
    & $RootPython $Runner
    exit $LASTEXITCODE
}

# ---------------------------------------------------------------------------
# schedule / unschedule - Task Scheduler (survives reboot)
# ---------------------------------------------------------------------------
function Invoke-Schedule {
    Write-Host "Installing auto-collection (Task Scheduler)"
    if (-not (Test-Path $RootPythonW)) {
        Write-Err "venv\Scripts\pythonw.exe not found. Run install.cmd first."
        exit 1
    }
    # pythonw.exe: no console window popping up every 5 minutes.
    $action = New-ScheduledTaskAction -Execute $RootPythonW `
        -Argument "`"$Runner`" --log `"$LogFile`"" -WorkingDirectory $ScriptDir
    # Repeats every 5 min indefinitely (no -RepetitionDuration) from now on,
    # plus a run at each logon so a reboot does not wait for the next slot.
    $every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Seconds $CollectIntervalSec)
    $atLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($every, $atLogon) `
        -Settings $settings -Principal $principal `
        -Description "WasteLess AWS collection & waste detection (every 5 min)" -Force | Out-Null
    Write-Ok "Scheduled task installed: $TaskName (every 5 min, while you are logged on)"

    # The in-process loop is redundant now.
    $collectorPid = Get-LivePid $CollectorPidFile
    if ($collectorPid) { Stop-Tree $collectorPid }
    Remove-Item $CollectorPidFile -ErrorAction SilentlyContinue
    Write-Host "  Disable with: wasteless unschedule" -ForegroundColor Cyan
}

function Invoke-Unschedule {
    if (Test-ScheduledTaskInstalled) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Ok "Scheduled task removed"
    } else {
        Write-Host "  No scheduled task to remove"
    }
}

# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
switch ($Command) {
    "start"      { Invoke-Start }
    "stop"       { Invoke-Stop }
    "logs"       { Invoke-Logs }
    "status"     { Invoke-Status }
    "collect"    { Invoke-Collect }
    "schedule"   { Invoke-Schedule }
    "unschedule" { Invoke-Unschedule }
    default {
        Write-Host "Usage: wasteless [command]"
        Write-Host ""
        Write-Host "Commands:"
        Write-Host "  start        Start the web UI in background (default)"
        Write-Host "  stop         Stop the web UI"
        Write-Host "  logs         View server logs (follow)"
        Write-Host "  status       Check if server + auto-collection are running"
        Write-Host "  collect      Run the complete collection and detection pipeline once"
        Write-Host "  schedule     Install auto-collection every 5 min (Task Scheduler, survives reboot)"
        Write-Host "  unschedule   Remove the scheduled auto-collection"
        Write-Host ""
        exit 1
    }
}
