# study/pull_archive.ps1 — mirror the GCS cold-archive locally for backtesting.
# Incremental: gsutil rsync WITHOUT -d, so it only downloads NEW chunks and never deletes local files.
# Usage:  powershell -ExecutionPolicy Bypass -File study\pull_archive.ps1
$ErrorActionPreference = 'Stop'
$GCS  = 'gs://smc-quant-archive/solusdt'          # <-- match ops/archive_buckets.py GCS_DEFAULT
$dest = Join-Path $PSScriptRoot 'archive_data'
if (-not (Test-Path $dest)) { New-Item -ItemType Directory -Path $dest | Out-Null }

Write-Host "[rsync] $GCS  ->  $dest"
gsutil -m rsync -r $GCS $dest

Write-Host "Local archive summary:"
Get-ChildItem $dest -Directory | Sort-Object Name | ForEach-Object {
    $n = @(Get-ChildItem $_.FullName -Filter *.jsonl.gz -ErrorAction SilentlyContinue).Count
    Write-Host ("  {0,-4} {1} chunk(s)" -f $_.Name, $n)
}
Write-Host "Load in Python:  from study.archive_loader import load_archive; bids, raws, gaps = load_archive('1m')"
