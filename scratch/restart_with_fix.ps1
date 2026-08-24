# Full stop-and-relaunch, so the running processes pick up new code.
#
# 2026-08-22: the verdict-durability fix (atomic writes + poison-proof reads)
# and the watchdog stall alarm are useless while every live process still holds
# the code it was started with. Pool workers inherit the module at pool
# creation, so nothing short of a relaunch installs it.
#
# Order matters. The watchdog dies FIRST or it restarts what is being stopped.
$ErrorActionPreference = 'Continue'
$scratch = 'C:\dev\DiscrepancyRecords\scratch'
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'

function Show($m) { Write-Output ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }

# 1. Watchdog first.
$lock = Join-Path $scratch 'watchdog.pid'
if (Test-Path $lock) {
  $held = 0
  [int]::TryParse((Get-Content $lock | Select-Object -First 1), [ref]$held) | Out-Null
  if ($held -gt 0) {
    Stop-Process -Id $held -Force -ErrorAction SilentlyContinue
    Show "stopped watchdog PID $held"
  }
  Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# 2. Everything else. Matched on the command line, because the multiprocessing
#    pool workers run `-c "from multiprocessing.spawn import spawn_main..."`
#    with no script name in them - a name-pattern kill misses all 16 and they
#    survive as orphans that keep spawning kissat.
$targets = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -and (
    $_.CommandLine -match 'run_campaign\.py' -or
    $_.CommandLine -match 'cube_wave2\.py' -or
    $_.CommandLine -match 'sample_prune\.py' -or
    $_.CommandLine -match 'check_pass\.py' -or
    $_.CommandLine -match 'check_and_prune\.py' -or
    $_.CommandLine -match 'multiprocessing\.spawn' -or
    $_.Name -eq 'kissat.exe' -or
    $_.Name -eq 'drat-trim-rebuilt.exe'
  )
}
Show ("stopping {0} campaign process(es)" -f @($targets).Count)
foreach ($t in $targets) {
  Stop-Process -Id $t.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 4

$left = @(Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -and (
    $_.CommandLine -match 'run_campaign\.py|cube_wave2\.py|sample_prune\.py|multiprocessing\.spawn' -or
    $_.Name -eq 'kissat.exe')
}).Count
Show "campaign processes still alive after kill: $left"

# 3. Clear what the kill left behind, with the now-hardened cleaner.
Show 'running crash_cleanup'
& $py (Join-Path $scratch 'crash_cleanup.py') wave274tot 2>&1 | ForEach-Object { Show "  $_" }

# 4. Relaunch. resume_campaign.ps1 is idempotent and re-asserts monitor-timeout 0.
Show 'relaunching campaign'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $scratch 'resume_campaign.ps1') 2>&1 |
  ForEach-Object { Show "  $_" }

Show 'relaunching watchdog'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $scratch 'start_watchdog.ps1') 2>&1 |
  ForEach-Object { Show "  $_" }

Show 'done'
