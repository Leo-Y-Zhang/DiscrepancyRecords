# Start (or restart) the a(17) campaign and its pruner, detached from any
# session that launches them. Safe to run repeatedly: it does nothing if the
# campaign is already running, already finished, or has stopped for a REASON.
#
# ⚠⚠ REWRITTEN 2026-08-20 19:2x AFTER FINDING THE REAL KILLER. This campaign
# died four times and the earlier diagnosis - "a Windows LOGOFF killed the
# session" - was true of the 19 Aug instance and WRONG as a general rule. The
# 20 Aug 19:15 death happened with explorer.exe running unbroken since 17:55,
# so there was no logoff at all. campaign.log had the answer in plain sight:
# every restart banner is prefixed with a literal ^C, which is what cmd.exe
# prints when a CTRL_C_EVENT arrives, and the 15 kissat workers found dead had
# each exited 0xC000013A = STATUS_CONTROL_C_EXIT.
#
# WMI creation reparents a process so a TREE kill cannot reach it, but the
# `cmd.exe /c ... >> log` wrapper it was handed still ALLOCATED A CONSOLE, and
# console control events are broadcast across a console regardless of parentage.
# Escaping the process tree is not escaping the console.
#
# So every launch below now goes through detach.py, which creates the process
# with DETACHED_PROCESS - no console at all, nothing to signal - and the Python
# entry points call console_immunity.ignore_console_signals() as well, a flag
# that is inherited by the 16 kissat children. WMI is kept as the OUTER layer
# purely so the launcher itself starts outside any caller's Job object.
#
# watchdog.ps1 calls this every five minutes, and
# Startup\resume-erdos-campaign.cmd starts the watchdog at logon.
$ErrorActionPreference = 'Stop'
$scratch = 'C:\dev\DiscrepancyRecords\scratch'
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

function Say($m) { Write-Output ("[{0}] {1}" -f $stamp, $m) }

if (Test-Path (Join-Path $scratch 'DONE.json')) {
  Say 'campaign FINISHED (DONE.json) - a session must harvest it'
  exit 0
}

# A deliberate stop is a RESULT, not a fault. Restarting through one would
# either loop forever on the same SAT cube or bury a check failure.
$fail = Join-Path $scratch 'wave274tot\CHECK_FAILURES.jsonl'
if (Test-Path $fail) {
  Say 'STOPPED: CHECK_FAILURES.jsonl exists - a proof did not verify. NOT restarting.'
  exit 0
}
$log = Join-Path $scratch 'campaign.log'
if (Test-Path $log) {
  $lines = Get-Content $log
  $starts = @(0..($lines.Count - 1) | Where-Object { $lines[$_] -like '*campaign orchestrator starting*' })
  $from = if ($starts.Count) { $starts[-1] } else { 0 }
  $halt = @($lines[$from..($lines.Count - 1)] | Where-Object { $_ -match 'HALT: .*SAT verdict' })
  if ($halt.Count) {
    Say ('STOPPED: ' + $halt[-1].Trim() + ' - a witness candidate. NOT restarting.')
    exit 0
  }
}

# Anything else that stopped the orchestrator is a fault (a BrokenProcessPool
# in the wave driver has happened once already), so restart it - but count the
# restarts, because a fault that repeats forever is not being fixed by retrying.
$countFile = Join-Path $scratch 'watchdog_restarts.txt'
$n = if (Test-Path $countFile) { [int](Get-Content $countFile | Select-Object -First 1) } else { 0 }

function Running($needle) {
  $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$needle*" }
  return ($procs | Measure-Object).Count -gt 0
}

# The script path is QUOTED in the real command line - ..."sample_prune.py" wave274tot...
# so a pattern spanning that boundary never matches, and every poll would have
# spawned another pruner and another checker until the box drowned. Match the
# script and the mode as two separate substrings.
function RunningMode($script, $mode) {
  $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$script*" -and
                   $_.CommandLine -like "*1 $mode*" }
  return ($procs | Measure-Object).Count -gt 0
}

# Launch with NO CONSOLE (detach.py) from OUTSIDE any Job object (WMI). The
# child PID comes back through a pid file because WMI gives no way to read the
# launcher's stdout, and because the launcher's own PID is worthless in a log -
# it has exited by the time anyone reads the line.
# $noWindow: for a process that itself spawns console children it cannot pass
# creation flags to - sample_prune.py, whose multiprocessing pool workers get
# creationflags 0 no matter what. A DETACHED parent hands each of those workers
# a brand new console WITH A VISIBLE WINDOW, which is how the checker's
# drat-trim ended up behind an X button (measured 2026-08-20). --no-window
# gives the parent one windowless console for the whole subtree to inherit.
function Start-Detached($logfile, $tag, $argv, $noWindow = $false) {
  $pidfile = Join-Path $scratch ("detach_{0}.pid" -f $tag)
  Remove-Item $pidfile -Force -ErrorAction SilentlyContinue
  $q = ($argv | ForEach-Object { '"' + $_ + '"' }) -join ' '
  $flag = ''
  if ($noWindow) { $flag = '--no-window ' }
  $cmd = '"{0}" "{1}\detach.py" {2}"{3}" "{4}" {5}' -f $py, $scratch, $flag, $logfile, $pidfile, $q
  $r = ([wmiclass]'Win32_Process').Create($cmd)
  $childPid = 0
  for ($i = 0; $i -lt 60; $i++) {
    if (Test-Path $pidfile) {
      $raw = Get-Content $pidfile -ErrorAction SilentlyContinue | Select-Object -First 1
      if ([int]::TryParse($raw, [ref]$childPid) -and $childPid -gt 0) { break }
    }
    Start-Sleep -Milliseconds 250
  }
  # A launch that did not happen must not read as a launch that did. The first
  # migration printed "PID 0 (rc 0)" for two of three processes and looked like
  # a success line; rc is the WMI launcher's return code and says nothing about
  # whether the real process started.
  $why = ''
  if ($childPid -le 0) {
    $errFile = $pidfile + '.error'
    if (Test-Path $errFile) { $why = ' - ' + ((Get-Content $errFile) -join '; ') }
    else { $why = ' - no pid file and no error file; launcher never ran' }
  }
  return @{ Rc = $r.ReturnValue; Pid = $childPid; Why = $why }
}

$verdicts = (Get-ChildItem (Join-Path $scratch 'wave274tot\verdicts') -Filter '*.json' -ErrorAction SilentlyContinue | Measure-Object).Count

if (Running 'run_campaign.py') {
  Say ("campaign running; {0} verdicts banked" -f $verdicts)
} elseif ($n -ge 25) {
  Say ("STOPPED: {0} automatic restarts already - something is failing repeatedly, not transiently. NOT restarting." -f $n)
  exit 0
} else {
  $r = Start-Detached $log 'campaign' @($py, '-u', (Join-Path $scratch 'run_campaign.py'))
  $n = $n + 1
  Set-Content -Path $countFile -Value $n -Encoding ascii
  Say ("RESTARTED campaign orchestrator: PID {0} (rc {1}); restart #{2}; {3} verdicts banked" -f $r.Pid, $r.Rc, $n, $verdicts)
}

if (RunningMode 'sample_prune.py' 'prune') {
  # quiet: the common case
} else {
  $plog = Join-Path $scratch 'pruneonly.log'
  $r = Start-Detached $plog 'pruner' @($py, '-u', (Join-Path $scratch 'sample_prune.py'),
                                       'wave274tot', '16384', '1600', '1', 'prune') $true
  Say ("RESTARTED prune-only pruner: PID {0} (rc {1})" -f $r.Pid, $r.Rc)
}

# The sampled proofs are checked WHILE the wave runs, on one core of sixteen,
# instead of waiting for campaign phase 3. Measured 2026-08-20: a 0.2 MB proof
# verifies in 1 s but a 181 MB one takes over nine minutes, so the pass is not
# the ~8 h the 136 s mean suggested. Front-loading costs the wave about 6% and
# takes the whole pass off the critical path between the wave and the
# confirmation run. Separate process from the pruner by design: a fast disk
# reclaim must never queue behind a multi-hour verification batch.
if (Test-Path (Join-Path $scratch 'PHASE3_ACTIVE')) {
  # Campaign phase 3 has taken ownership of proof checking and runs it on 14
  # threads. Restarting the one-worker front-loaded checker now would put two
  # checkers on the same sampled cube ids, duplicating multi-hour drat-trim
  # runs. run_campaign.py removes this marker when the phase ends.
} elseif (RunningMode 'sample_prune.py' 'check') {
  # quiet: the common case
} else {
  $clog = Join-Path $scratch 'checker.log'
  $r = Start-Detached $clog 'checker' @($py, '-u', (Join-Path $scratch 'sample_prune.py'),
                                        'wave274tot', '16384', '1600', '1', 'check') $true
  Say ("RESTARTED sample checker: PID {0} (rc {1})" -f $r.Pid, $r.Rc)
}

# Sleep would only suspend the run, but there is no reason to pay the pause.
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null
# The one that actually governs this box. MEASURED 2026-08-20: with
# SetThreadExecutionState asserted and returning 0x80000000 the machine still
# entered Modern Standby four times on "Idle Timeout" and the wave fell from
# ~230/h to 121/h. On a Modern Standby system the transition follows the SCREEN
# going off, so the display timeout is the lever - and nothing was re-asserting
# it, which is exactly how the first version got lost. The OLED screensaver
# still runs on idle, so the panel is not being traded away.
# WARNING: restore the operator's own value (600 s on AC) at harvest.
powercfg /change monitor-timeout-ac 0 | Out-Null
