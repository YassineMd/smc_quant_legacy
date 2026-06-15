<#
.SYNOPSIS
    Push the OrderFlowDaemon source to the GCP VM (Debian 12) for a 24/7 run.

.DESCRIPTION
    Repeatable update deploy. Three steps:
      1. Stop the local daemon and CHECKPOINT (flush) the SQLite WAL, so the
         uploaded data\history.db is a single self-consistent file. (A Windows
         Stop-Process is a hard kill that skips the daemon's SIGINT flush, so we
         flush the WAL explicitly here.)
      2. Ensure the remote code directory exists (idempotent mkdir over SSH).
      3. Upload app\, data\, requirements-daemon.txt, run_daemon.py via gcloud scp.

    One-time VM setup (apt, venv, pip, the systemd unit) lives in DEPLOYMENT.md.
    After this script: on the VM run `sudo systemctl restart orderflow`.

    Runs from anywhere - paths resolve relative to this script's folder (repo root).
#>

# ----------------------------- config -----------------------------
$Project    = "yass-chart"
$Zone       = "europe-west9-b"
$VM         = "smc-quant-eu"
$RemoteUser = "yassine.mdouari"                       # <-- CHANGE to your VM username
$RemoteDir  = "/home/$RemoteUser/OrderFlowPlatform"
$Port       = 9999

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }
function Assert-LastExit($desc) {
    if ($LASTEXITCODE -ne 0) { throw "$desc failed (exit code $LASTEXITCODE)." }
}

# --------------------------- preflight ----------------------------
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "gcloud CLI not found on PATH. Install the Google Cloud SDK and run 'gcloud auth login'."
}
foreach ($p in @("app", "requirements-daemon.txt", "run_daemon.py")) {
    if (-not (Test-Path (Join-Path $PSScriptRoot $p))) {
        throw "Missing '$p' in $PSScriptRoot. Run deploy.ps1 from the project root."
    }
}

# ---------- 1. stop the local daemon + flush the SQLite WAL ----------
Info "Stopping local daemon on port $Port (so the SQLite file is consistent for upload)..."
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $procId = $conn.OwningProcess | Select-Object -First 1
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    try { Wait-Process -Id $procId -Timeout 10 -ErrorAction SilentlyContinue } catch {}
    Ok "Daemon (PID $procId) stopped."
} else {
    Ok "No local daemon was running on port $Port."
}

$db = Join-Path $PSScriptRoot "data\history.db"
if (Test-Path $db) {
    Info "Flushing the SQLite WAL into history.db (wal_checkpoint TRUNCATE)..."
    if (Get-Command python -ErrorAction SilentlyContinue) {
        python -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.commit(); c.close()" "$db"
        if ($LASTEXITCODE -eq 0) {
            Ok "WAL flushed; history.db is self-contained."
        } else {
            Warn "Checkpoint returned exit $LASTEXITCODE. The full data\ upload still carries the -wal sidecar, so the cloud recovers it."
        }
    } else {
        Warn "python not on PATH; skipping explicit checkpoint. The data\ upload still carries the -wal sidecar."
    }
} else {
    Warn "No data\history.db yet - the cloud daemon will cold-start and build its own history."
}

# ---------------- 2. ensure the remote directory exists ----------------
Info "Preparing remote directory on $VM ..."
$prep = "mkdir -p $RemoteDir && echo REMOTE_DIR_READY"
gcloud compute ssh "$RemoteUser@$VM" --project=$Project --zone=$Zone "--command=$prep"
Assert-LastExit "Remote prep (gcloud compute ssh)"
Ok "Remote directory ready: $RemoteDir"

# ------------------------- 3. upload the source -------------------------
$sources = @("app", "requirements-daemon.txt", "run_daemon.py")
if (Test-Path (Join-Path $PSScriptRoot "data")) {
    $sources += "data"
} else {
    Warn "No local data\ folder - uploading code only (clean cloud start)."
}
Info ("Uploading to {0}:{1} ... ({2})" -f $VM, $RemoteDir, ($sources -join ", "))
$sourceFiles = $sources -join " "
Invoke-Expression "gcloud compute scp --recurse --project=$Project --zone=$Zone $sourceFiles $RemoteUser@${VM}:$RemoteDir/"
Assert-LastExit "Upload (gcloud compute scp)"
Ok "Upload complete."

# ------------------------------ next steps ------------------------------
Write-Host ""
Info "Deploy finished. SSH in and apply the update on the VM:"
$sshHint = "gcloud compute ssh $RemoteUser@$VM --project=$Project --zone=$Zone"
Write-Host "    $sshHint" -ForegroundColor Gray
Write-Host "    sudo systemctl restart orderflow" -ForegroundColor Gray
Write-Host "    journalctl -u orderflow -n 30 --no-pager      # expect REHYDRATE then LISTENING" -ForegroundColor Gray
Write-Host "    # deps changed? first run: ./venv/bin/pip install -r requirements-daemon.txt" -ForegroundColor Gray
