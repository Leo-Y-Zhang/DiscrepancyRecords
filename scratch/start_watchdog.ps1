# Start the campaign watchdog so that closing a terminal cannot kill it.
#
# 2026-08-20: the old path was `start "" /min powershell.exe -File watchdog.ps1`,
# which gives the watchdog a CONSOLE. At 19:15 a console control event took the
# orchestrator, all 16 kissat workers AND the watchdog down together - the
# restarter died in the same instant as the thing it was restarting, so nothing
# came back. campaign.log records it as a literal ^C in front of each restart
# banner; the workers exited 0xC000013A (STATUS_CONTROL_C_EXIT).
#
# ⚠ The watchdog is watchdog.PY now, not watchdog.ps1. The fix is to run with no
# console, and POWERSHELL DOES NOT RUN THAT WAY - launched with DETACHED_PROCESS
# it starts, exits silently and executes nothing. Verified twice: a one-line
# test script never wrote its output file, and the 19:30 launch reported a PID
# and then never wrote a line to watchdog.log. Python runs fine detached.
$ErrorActionPreference = 'Stop'
$scratch = 'C:\dev\DiscrepancyRecords\scratch'
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$log = Join-Path $scratch 'watchdog_stdio.log'
$pidfile = Join-Path $scratch 'detach_watchdog.pid'

# watchdog.py holds a PID lock whose holder must be alive AND be a python
# process, so re-running this is safe; there is just no point spawning a
# launcher that will only exit again.
$lockFile = Join-Path $scratch 'watchdog.pid'
if (Test-Path $lockFile) {
  $held = 0
  [int]::TryParse((Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1), [ref]$held) | Out-Null
  if ($held -gt 0) {
    $p = Get-Process -Id $held -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -match 'python') {
      Write-Output ("watchdog already running (PID {0}) - nothing to do" -f $held)
      exit 0
    }
  }
}

Remove-Item $pidfile -Force -ErrorAction SilentlyContinue
Remove-Item ($pidfile + '.error') -Force -ErrorAction SilentlyContinue
$argv = @($py, '-u', (Join-Path $scratch 'watchdog.py'))
$q = ($argv | ForEach-Object { '"' + $_ + '"' }) -join ' '
$cmd = '"{0}" "{1}\detach.py" "{2}" "{3}" {4}' -f $py, $scratch, $log, $pidfile, $q

# WMI as the outer layer so the launcher itself starts outside any Job object
# belonging to whatever called this.
$r = ([wmiclass]'Win32_Process').Create($cmd)
$childPid = 0
for ($i = 0; $i -lt 60; $i++) {
  if (Test-Path $pidfile) {
    $raw = Get-Content $pidfile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ([int]::TryParse($raw, [ref]$childPid) -and $childPid -gt 0) { break }
  }
  Start-Sleep -Milliseconds 250
}

# ⚠ A launcher PID is not a running watchdog. The 19:30 attempt printed a PID
# for a PowerShell watchdog that never executed a line. Confirm it actually took
# its lock and logged its own start before reporting success.
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  if (Test-Path $lockFile) {
    $held = 0
    [int]::TryParse((Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1), [ref]$held) | Out-Null
    if ($held -eq $childPid -and $childPid -gt 0) { $ok = $true; break }
  }
  Start-Sleep -Milliseconds 250
}
if ($ok) {
  Write-Output ("watchdog RUNNING detached: PID {0} (lock taken, start logged)" -f $childPid)
} else {
  $why = ''
  if (Test-Path ($pidfile + '.error')) { $why = ' - ' + ((Get-Content ($pidfile + '.error')) -join '; ') }
  Write-Output ("watchdog FAILED TO START: launcher rc {0}, child pid {1}{2}" -f $r.ReturnValue, $childPid, $why)
  exit 1
}
