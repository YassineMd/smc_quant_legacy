# Cycle archive -- the DAILY scheduled harvest (user 2026-09-24: "Yes to the daily scheduled harvest").
#
# Registers (or replaces) the Windows task "SMC Cycle Archive Harvest" for the CURRENT user:
#   * daily at 18:00 PC time, and -- if the PC was off or asleep then -- as soon as it is next available
#     (StartWhenAvailable), so one missed day does not open a gap (the daemon keeps 72 h; a harvest covers ~68 h);
#   * runs study\cycle_archive\run_collect.cmd in a MINIMIZED window (log: study\cycle_archive\logs\collect_YYYYMM.log);
#   * only while the user is logged on (no stored password); one instance at a time; stopped after 45 min.
#
#   powershell -ExecutionPolicy Bypass -File study\cycle_archive\register_daily_task.ps1            # register / update
#   powershell -ExecutionPolicy Bypass -File study\cycle_archive\register_daily_task.ps1 -Remove    # remove it
param([switch]$Remove, [string]$At = "18:00")
$ErrorActionPreference = "Stop"
$name = "SMC Cycle Archive Harvest"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Output "removed: $name"
    exit 0
}
$cmd = Join-Path $PSScriptRoot "run_collect.cmd"
if (-not (Test-Path $cmd)) { throw "not found: $cmd" }
$act = New-ScheduledTaskAction -Execute "cmd.exe" -Argument ('/c start "" /min "' + $cmd + '"') -WorkingDirectory $PSScriptRoot
$trg = New-ScheduledTaskTrigger -Daily -At $At
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$desc = "Harvests the SMC flow cycles, the 15 s wall grid and the 1 s bins before the daemon's 72 h window rolls past them " +
        "(study\cycle_archive\collect_cycles.py -> study\cycle_archive\data + gs://smc-quant-archive/solusdt/cycles/)."
Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg -Settings $set -Description $desc -Force | Out-Null
$t = Get-ScheduledTask -TaskName $name
$i = $t | Get-ScheduledTaskInfo
Write-Output ("registered: {0} | state {1} | next run {2}" -f $name, $t.State, $i.NextRunTime)
