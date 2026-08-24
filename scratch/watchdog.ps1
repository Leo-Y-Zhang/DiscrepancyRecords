# ⛔⛔ SUPERSEDED 2026-08-20 19:37 BY watchdog.py - DO NOT START THIS FILE.
#
# The campaign's four deaths were all a console control event (campaign.log
# prefixes every restart banner with a literal ^C; the workers exited
# 0xC000013A = STATUS_CONTROL_C_EXIT). The fix is to run with NO CONSOLE, and
# POWERSHELL CANNOT: launched with DETACHED_PROCESS it starts, exits silently
# and executes nothing - verified twice, once with a one-line script that never
# wrote its output file and once by a 19:30 launch that reported a PID and then
# never wrote a line to watchdog.log. So the watchdog is Python now.
#
# Kept only as the record of the logic that was ported. Start it via
# start_watchdog.ps1, which launches watchdog.py through detach.py.
#
# ---- original header follows ----
# Keep the a(17) campaign alive for the ~5 days it needs, unattended.
#
# The campaign orchestrator is a single process running a chain of multi-day
# phases. If its wave driver dies - a BrokenProcessPool has already happened
# once in this campaign - the orchestrator falls through to "HALT: only N/16384
# UNSAT" and EXITS, and nothing was watching. Over five days that is the most
# likely way to lose the run, more likely than the logoff that cost 4.5 h.
#
# So: every five minutes, run the one-shot resume script. It restarts whatever
# is not running, refuses to restart through a SAT verdict or a check failure
# (those are results), and gives up after 25 restarts rather than thrashing.
$ErrorActionPreference = 'Continue'
$scratch = $PSScriptRoot
$log = Join-Path $scratch 'watchdog.log'

# ⚠⚠ 2026-08-20: the thing that kept killing this campaign was a console Ctrl+C,
# not a logoff (campaign.log prefixes every restart banner with a literal ^C).
# The watchdog is the last line of defence and must be the LEAST killable thing
# on the box, so it ignores Ctrl+C too - otherwise the signal that takes down
# the campaign takes down its restarter in the same instant, which is exactly
# what happened at 19:15 when both went at once. It is also started with no
# console at all (start_watchdog.ps1 / detach.py); this is the second layer.
try {
  Add-Type -Name Con -Namespace Win32 -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool SetConsoleCtrlHandler(IntPtr handler, bool add);
'@ -ErrorAction SilentlyContinue
  [void][Win32.Con]::SetConsoleCtrlHandler([IntPtr]::Zero, $true)
} catch { }

# One watchdog only: two polling in step would both see "campaign not running"
# in the same instant and start two orchestrators on one wave directory.
#
# This guard used to match any process whose command line CONTAINED
# "watchdog.ps1", excluding its own PID. That is broken, and it cost the run:
# the wrapper that LAUNCHES this script also has "watchdog.ps1" in its command
# line, so a freshly started watchdog saw its own launcher, called it an
# incumbent, and exited - leaving nothing watching while the campaign was dead.
# A name substring is not an identity. Use a PID lock and prove the holder is
# alive and is actually a PowerShell process.
$lockFile = Join-Path $scratch 'watchdog.pid'
if (Test-Path $lockFile) {
  $held = 0
  [int]::TryParse((Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1), [ref]$held) | Out-Null
  if ($held -gt 0 -and $held -ne $PID) {
    $p = Get-Process -Id $held -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -match 'powershell|pwsh') {
      ("[{0}] watchdog already running (PID {1}, started {2}) - this one exits" -f
        (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $held, $p.StartTime) |
        Add-Content $log -Encoding ascii
      exit 0
    }
  }
}
Set-Content -Path $lockFile -Value $PID -Encoding ascii

("[{0}] watchdog started (PID {1}), polling every 300 s" -f
  (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $PID) | Add-Content $log -Encoding ascii

# Keep the machine out of Modern Standby. MEASURED, not assumed: the box idled
# into Modern Standby at 23:53:44 on 19 Aug and stayed there until the lid was
# opened at 03:07:09, and verdict mtimes across that window show the wave ran at
# ~132/h against ~250/h awake - standby did not stop the compute, it HALVED it.
# Over four nights that is more than a day of wall clock.
#
# powercfg's standby-timeout is not the governing setting on a Modern Standby
# machine, and /requests needs admin. SetThreadExecutionState does not: it is
# the documented way for a plain user process to say "the system is in use".
#
# ⚠ MEASURED 2026-08-20 AND IT IS NOT ENOUGH. With this asserted and returning
# 0x80000000, the box STILL entered Modern Standby at 07:08, 08:09, 09:30 and
# 09:35, every one of them "Reason: Idle Timeout", and the wave fell from
# ~230/h to 121-156/h. On a Modern Standby system the transition follows the
# SCREEN going off, and an execution-state request does not hold it open.
# The lever that does is `powercfg /change monitor-timeout-ac 0` - the display
# never turns off, so there is no screen-off to follow. The OLED screensaver
# still runs on idle and protects the panel.
# ⚠ This assertion is kept anyway: it costs nothing and it is the documented
# guard against the ordinary idle-sleep path. It is simply not sufficient here.
# ⚠ Restore the operator's display timeout (it was 10 min on AC) at harvest.
Add-Type -Name Power -Namespace Win32 -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
'@ -ErrorAction SilentlyContinue
# PowerShell parses 0x80000000 as a SIGNED Int32 (-2147483648), so -bor yields a
# negative number and the UInt32 marshal throws. Cast every flag explicitly.
$ES_CONTINUOUS = [uint32]2147483648      # 0x80000000
$ES_SYSTEM_REQUIRED = [uint32]1          # 0x00000001
$ES_AWAYMODE_REQUIRED = [uint32]64       # 0x00000040

function Assert-Awake {
  # Returns the previous state (non-zero) on success, 0 on failure. Report what
  # the call actually returned - the first version of this treated a $null from
  # a throwing call as "ok", which is an oracle that cannot fail.
  try {
    $flags = [uint32]($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED -bor $ES_AWAYMODE_REQUIRED)
    $r = [Win32.Power]::SetThreadExecutionState($flags)
    if ($r -eq 0) {
      # Away mode is not supported everywhere; fall back to system-required.
      $r = [Win32.Power]::SetThreadExecutionState([uint32]($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED))
    }
    return [uint32]$r
  } catch {
    return [uint32]0
  }
}
$r = Assert-Awake
("[{0}] keep-awake: SetThreadExecutionState returned 0x{1:X8} - {2}" -f
  (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $r,
  $(if ($r -ne 0) { 'ASSERTED' } else { 'FAILED, the box can idle into standby at half speed' })) |
  Add-Content $log -Encoding ascii

# A terminal state must be visible to a session that was not here when it
# happened. Background monitors in the assistant's own harness do NOT survive
# between turns - two were killed on 20 Aug - so the durable notice is a file on
# disk, written once, named so nobody can miss it.
$attention = Join-Path $scratch 'CAMPAIGN_ATTENTION.txt'
function Raise-Attention($reason) {
  if (-not (Test-Path $attention)) {
    ("{0}`r`n{1}`r`n" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $reason) |
      Set-Content $attention -Encoding ascii
    # The operator is closing every window and coming back in about a week, so
    # there is no session left to notify them and no scheduled task available
    # (schtasks /create is Access-denied on this non-admin box). A file on disk
    # only works for someone who thinks to look for it. This watchdog runs in
    # desktop session 9 - the same one as explorer - so it can put the notice
    # where it cannot be missed: on the screen. Notepad survives this process
    # exiting, which matters because the DONE path breaks out of the loop
    # immediately after raising this.
    try { Start-Process notepad.exe -ArgumentList "`"$attention`"" } catch { }
  }
}

$lastBeat = (Get-Date).AddHours(-2)
while ($true) {
  try {
    # Re-assert every pass: ES_CONTINUOUS is per-thread state, and a lost
    # assertion is silent - the box would simply start idling into standby again
    # at half speed with nothing in any log to say why.
    [void](Assert-Awake)

    # Once an hour, say so. Without this the log is events-only, and silence
    # cannot be told apart from a dead watchdog - which is exactly the state
    # this campaign was found in at 03:09.
    if (((Get-Date) - $lastBeat).TotalMinutes -ge 60) {
      $n = (Get-ChildItem (Join-Path $scratch 'wave274tot\verdicts') -Filter '*.json' -ErrorAction SilentlyContinue | Measure-Object).Count
      $k = (Get-Process kissat -ErrorAction SilentlyContinue | Measure-Object).Count
      $tn = 0
      $tp = Join-Path $scratch 'wave274tot\transcripts.jsonl'
      if (Test-Path $tp) { $tn = (Get-Content $tp | Measure-Object -Line).Lines }
      # Modern Standby halves the wave and leaves no trace in this log unless it
      # is put there. The keep-awake assertion above returns success while the
      # box sleeps anyway, so the event log is the only honest witness - count
      # the entries since the last beat rather than trusting the API.
      $sb = 0
      try {
        $sb = @(Get-WinEvent -FilterHashtable @{
          LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'
          Id = 506; StartTime = $lastBeat } -ErrorAction SilentlyContinue).Count
      } catch { $sb = -1 }
      $sbNote = if ($sb -gt 0) { ", STANDBY x$sb since last beat (wave runs at ~half speed asleep)" }
                elseif ($sb -lt 0) { ', standby count unavailable' } else { '' }
      ("[{0}] alive: {1}/16384 verdicts, {2} solvers, {3} proofs verified{4}" -f
        (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $n, $k, $tn, $sbNote) | Add-Content $log -Encoding ascii
      $lastBeat = Get-Date
    }
    $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $scratch 'resume_campaign.ps1') 2>&1
    foreach ($line in $out) {
      $t = "$line".Trim()
      # Only record something that happened. A five-day heartbeat log of
      # "still running" is a log nobody reads.
      if ($t -and ($t -notmatch 'campaign running;')) {
        $t | Add-Content $log -Encoding ascii
      }
      # A deliberate stop is the one thing that needs a person. Leave a marker
      # a later session cannot walk past, then keep polling rather than exiting,
      # so the log still shows the box is alive.
      if ($t -match 'STOPPED:') { Raise-Attention $t }
    }
    if (Test-Path (Join-Path $scratch 'DONE.json')) {
      ("[{0}] campaign finished - watchdog exiting" -f
        (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) | Add-Content $log -Encoding ascii
      Raise-Attention @'
THE ERDOS #176 CAMPAIGN HAS FINISHED. Nothing is broken - this is the good one.

The computation for a(17) = N(17,2) = 274 is complete and DONE.json is written.
Every cube was solved twice, under two independent encodings.

WHAT IS LEFT is not compute. It needs one Claude Code session, a few hours:
import the evidence for both waves, write the claim, run the gate, push.

TO START IT: open Claude Code on this laptop and say exactly

    continue with erdos

That phrase is enough - the session will pick this up with no explanation from
you. Nothing is time-critical and nothing degrades while it waits.

You can close this window.
'@
      break
    }
  } catch {
    ("[{0}] watchdog error: {1}" -f
      (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $_.Exception.Message) |
      Add-Content $log -Encoding ascii
  }
  Start-Sleep -Seconds 300
}
