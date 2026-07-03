# study/pull_snapshot.ps1
# Phase-1 CONSISTENT-SNAPSHOT pull of the VM's history.db — READ-ONLY, daemon untouched.
#
# Why cadence: the 10k/tf cap makes 1m history a ~4-day ROLLING window, and each snapshot freezes
# ONE window forever. Run this every ~3 days to accumulate non-overlapping windows offline for the
# barrier study. Zero daemon changes, zero config writes; the only remote write is rm on /tmp.
#
# Usage:  powershell -ExecutionPolicy Bypass -File study\pull_snapshot.ps1
$ErrorActionPreference = 'Stop'
$VM = 'smc-quant-eu'; $ZONE = 'europe-west9-b'; $PROJECT = 'yass-chart'
$TMP = '/tmp/history_snapshot.db'

$stamp = Get-Date -Format 'yyyyMMdd'
$dataDir = Join-Path $PSScriptRoot 'data'
if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Path $dataDir | Out-Null }
$out = Join-Path $dataDir ("history_snapshot_{0}.db" -f $stamp)

# 1) transaction-consistent copy on the VM. There is no sqlite3 CLI on the box, so python3 does the
#    VACUUM INTO (WAL-safe: a read snapshot never blocks the daemon's writes). The python is base64'd
#    and piped to `python3 -`, so NOTHING needs quote-escaping through PowerShell -> gcloud -> bash.
# ABSOLUTE daemon path + sudo: gcloud's SSH username (yassine_mdouari) is NOT the service user
# (yassine.mdouari), so ~ no longer resolves to the platform dir. The daemon's db is identity-
# independent; sudo reads it, chmod 644 lets the (non-root) scp step fetch the copy.
$py = "import sqlite3, os" + "`n" +
      "src = '/home/yassine.mdouari/OrderFlowPlatform/data/history.db'" + "`n" +
      "c = sqlite3.connect(src)" + "`n" +
      "c.execute('VACUUM INTO ' + chr(39) + '$TMP' + chr(39))" + "`n" +
      "c.close()" + "`n" +
      "print('vacuumed', src)"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
$mkSnap = "sudo rm -f $TMP && echo $b64 | base64 -d | sudo python3 - && sudo chmod 644 $TMP"

Write-Host "[1/3] VACUUM INTO on $VM (read-only; daemon untouched) ..."
gcloud compute ssh $VM --zone $ZONE --project $PROJECT --command $mkSnap

Write-Host "[2/3] scp -> $out ..."
gcloud compute scp ("{0}:{1}" -f $VM, $TMP) $out --zone $ZONE --project $PROJECT

Write-Host "[3/3] cleanup $TMP on $VM (the one permitted remote write) ..."
gcloud compute ssh $VM --zone $ZONE --project $PROJECT --command "sudo rm -f $TMP"

$mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host "Snapshot saved: $out ($mb MB). Run again in ~3 days to bank the next window."
