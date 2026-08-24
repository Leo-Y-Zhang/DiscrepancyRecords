"""Keep the a(17) campaign alive for the days it still needs, unattended.

A PYTHON PORT OF watchdog.ps1, AND THE REASON IS MEASURED, NOT STYLISTIC.
The fix for the thing that has killed this campaign four times is to run with no
console at all (see detach.py). powershell.exe DOES NOT RUN THAT WAY: launched
with DETACHED_PROCESS it starts, exits silently and executes nothing - verified
directly on 2026-08-20 with a one-line script that never wrote its output file,
and again by the 19:30 watchdog launch which reported a PID and then never
appeared in watchdog.log. Python runs fine with no console, so the watchdog is
Python now.

Behaviour is deliberately identical to watchdog.ps1, whose guards were observed
working rather than assumed:

  - single instance, via a PID lock whose holder must be ALIVE and be a python
    process. A name substring is not an identity: the old guard matched any
    command line CONTAINING "watchdog.ps1", which included its own launcher, so
    a fresh watchdog saw a phantom incumbent and exited leaving nothing watching.
  - every 300 s, run resume_campaign.ps1, which restarts whatever is missing and
    refuses to restart through a SAT verdict or a check failure.
  - a STOPPED: line raises CAMPAIGN_ATTENTION.txt and opens it in Notepad on the
    operator's screen, because a terminal state must be visible to someone who
    was not here when it happened.
  - DONE.json raises the same notice with the good-news text and exits.
  - an hourly heartbeat, so silence can be told apart from a dead watchdog -
    which is exactly the state this campaign was found in at 03:09 on 20 Aug.
  - keep-awake re-asserted every poll (per-thread state, silently lost otherwise).

⚠ resume_campaign.ps1 is PowerShell and therefore needs a console, so it is run
as a SHORT-LIVED child with CREATE_NO_WINDOW - its own private, invisible
console that no terminal is attached to. The long-lived process, this one, still
has none.
"""
import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from console_immunity import ignore_console_signals  # noqa: E402

ignore_console_signals()

SCRATCH = HERE
LOG = os.path.join(SCRATCH, "watchdog.log")
LOCK = os.path.join(SCRATCH, "watchdog.pid")
ATTENTION = os.path.join(SCRATCH, "CAMPAIGN_ATTENTION.txt")
DONE = os.path.join(SCRATCH, "DONE.json")
RESUME = os.path.join(SCRATCH, "resume_campaign.ps1")
VERDICTS = os.path.join(SCRATCH, "wave274tot", "verdicts")
TRANSCRIPTS = os.path.join(SCRATCH, "wave274tot", "transcripts.jsonl")
VERDICTS2 = os.path.join(SCRATCH, "wave274", "verdicts")

POLL_SECONDS = 300

#: A restart loop is invisible to every other alarm here. On 2026-08-22 two
#: zero-byte verdict files made the orchestrator die one second after each
#: restart; resume_campaign reported "RESTARTED ... (rc 0)" because the SPAWN
#: succeeded, and CAMPAIGN_ATTENTION fires only on a SAT verdict, a check
#: failure or DONE.json. None of those is a crash loop, so the Notepad notice
#: would never have opened and the campaign would have sat at 14,805/16,384
#: for days looking healthy in the log.
#:
#: The alarm needs BOTH conditions. No-progress alone false-positives on the
#: phase 3 tail, where a single proof can take 66 minutes across 14 workers.
#: Repeated restarts alone false-positive on an ordinary bad night. Together
#: they are only ever the failure above: a healthy campaign is never restarted
#: three times while producing absolutely nothing.
STALL_SECONDS = 2700
STALL_RESTARTS = 3

STALL_NOTICE = """THE ERDOS #176 CAMPAIGN IS STUCK IN A RESTART LOOP. It needs a session.

It is being restarted over and over and is producing nothing. This is NOT the
mathematics failing and NOT a SAT verdict - it is a fault in the plumbing, and
everything computed so far is safe on disk.

TO FIX IT: open Claude Code on this laptop and say exactly

    continue with erdos

Nothing is time-critical, but every hour it stays stuck is an hour of compute
not happening.
"""
CREATE_NO_WINDOW = 0x08000000

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040

DONE_NOTICE = """THE ERDOS #176 CAMPAIGN HAS FINISHED. Nothing is broken - this is the good one.

The computation for a(17) = N(17,2) = 274 is complete and DONE.json is written.
Every cube was solved twice, under two independent encodings.

WHAT IS LEFT is not compute. It needs one Claude Code session, a few hours:
import the evidence for both waves, write the claim, run the gate, push.

TO START IT: open Claude Code on this laptop and say exactly

    continue with erdos

That phrase is enough - the session will pick this up with no explanation from
you. Nothing is time-critical and nothing degrades while it waits.

You can close this window.
"""


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def say(msg):
    with open(LOG, "a", encoding="ascii", errors="replace") as fh:
        fh.write(f"[{stamp()}] {msg}\n")


def pid_alive_python(pid):
    """True only if pid is running AND is a python process.

    Both halves matter. PIDs are reused, and the guard this replaces trusted a
    name substring instead of an identity and cost the run a night of compute.
    """
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").Name"],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
            timeout=60,
        ).stdout.strip().lower()
    except Exception:
        return False
    return out.startswith("python")


def take_lock():
    if os.path.exists(LOCK):
        try:
            with open(LOCK, encoding="ascii") as fh:
                held = int(fh.read().strip() or "0")
        except (OSError, ValueError):
            held = 0
        if held and held != os.getpid() and pid_alive_python(held):
            say(f"watchdog already running (PID {held}) - this one exits")
            return False
    with open(LOCK, "w", encoding="ascii") as fh:
        fh.write(str(os.getpid()))
    return True


def assert_awake():
    """Ask Windows to stay awake. Returns the API's actual return value.

    ⚠ Report what the call returned, never a bare 'ok'. The first PowerShell
    version treated a $null from a throwing call as success - an oracle that
    could not fail. ⚠ And this is NOT sufficient on this box: it returned
    0x80000000 while the machine entered Modern Standby four times on Idle
    Timeout. The lever that works is `powercfg /change monitor-timeout-ac 0`,
    re-asserted by resume_campaign.ps1 on every poll. This is kept because it is
    the documented guard against the ordinary idle-sleep path and costs nothing.
    """
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
        k32.SetThreadExecutionState.restype = ctypes.c_uint32
        r = k32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)
        if r == 0:
            r = k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        return r
    except Exception:
        return 0


def raise_attention(reason):
    """Put a terminal state where a person cannot walk past it."""
    if os.path.exists(ATTENTION):
        return
    with open(ATTENTION, "w", encoding="ascii", errors="replace") as fh:
        fh.write(f"{stamp()}\r\n{reason}\r\n")
    # This process runs in the interactive desktop session, so a GUI launch is
    # visible. Notepad outlives this process, which matters because the DONE
    # path exits immediately after.
    try:
        subprocess.Popen(["notepad.exe", ATTENTION], close_fds=True)
    except Exception:
        pass


def count_files(path):
    try:
        return sum(1 for f in os.listdir(path) if f.endswith(".json"))
    except OSError:
        return 0


def count_lines(path):
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def is_stalled(stalled_seconds, restarts):
    """The stall decision, in ONE place.

    The poll loop and the test both call this. A test that re-implements the
    condition it is checking proves only that the author can write it twice -
    and this campaign has already shipped a verifier that checked the four
    processes that were never in danger.
    """
    return stalled_seconds >= STALL_SECONDS and restarts >= STALL_RESTARTS


def progress():
    """One number that goes up whenever ANY phase is doing work.

    It must span all four phases or the stall alarm false-fires between them:
    phases 1-2 grow wave274tot verdicts, phase 3 grows transcripts.jsonl and no
    verdicts at all for ~29 h, phase 4 grows wave274 verdicts. Watching only
    the wave would have called phase 3 a stall every single time.
    """
    return (count_files(VERDICTS) + count_files(VERDICTS2)
            + count_lines(TRANSCRIPTS))


def count_solvers():
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "@(Get-Process kissat -ErrorAction SilentlyContinue).Count"],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
            timeout=60,
        ).stdout.strip()
        return int(out or 0)
    except Exception:
        return -1


def standby_since(since_epoch):
    """Modern Standby halves the wave and leaves no trace unless it is logged.

    The keep-awake call returns success while the box sleeps anyway, so the
    event log is the only honest witness.
    """
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(since_epoch))
    ps = ("@(Get-WinEvent -FilterHashtable @{LogName='System';"
          "ProviderName='Microsoft-Windows-Kernel-Power';Id=506;"
          f"StartTime=[datetime]'{since}'}} -ErrorAction SilentlyContinue).Count")
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
            timeout=120,
        ).stdout.strip()
        return int(out or 0)
    except Exception:
        return -1


def run_resume():
    """PowerShell needs a console, so give this short-lived child a private one."""
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", RESUME],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
            timeout=600,
        )
        return (res.stdout or "") + (res.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return f"watchdog error: resume_campaign failed: {exc}"


def main():
    if not take_lock():
        return 0
    say(f"watchdog started (PID {os.getpid()}, python, no console), polling every {POLL_SECONDS} s")
    r = assert_awake()
    state = ("ASSERTED" if r
             else "FAILED, the box can idle into standby at half speed")
    say(f"keep-awake: SetThreadExecutionState returned 0x{r:08X} - {state}")

    last_beat = time.time() - 3600
    last_progress, progress_at, restarts = progress(), time.time(), 0
    while True:
        try:
            assert_awake()

            # Progress, not process count. A crash-looping orchestrator is
            # "running" at every poll and produces nothing.
            now_progress = progress()
            if now_progress != last_progress:
                last_progress, progress_at, restarts = now_progress, time.time(), 0

            if time.time() - last_beat >= 3600:
                sb = standby_since(last_beat)
                note = (f", STANDBY x{sb} since last beat (wave runs at ~half speed asleep)"
                        if sb > 0 else ", standby count unavailable" if sb < 0 else "")
                say(f"alive: {count_files(VERDICTS)}/16384 verdicts, "
                    f"{count_solvers()} solvers, "
                    f"{count_lines(TRANSCRIPTS)} proofs verified{note}")
                last_beat = time.time()

            for line in run_resume().splitlines():
                t = line.strip()
                if not t:
                    continue
                # Only record something that happened. A five-day log of "still
                # running" is a log nobody reads.
                if "campaign running;" not in t:
                    say(t)
                if "STOPPED:" in t:
                    raise_attention(t)
                if "RESTARTED campaign orchestrator" in t:
                    restarts += 1

            stalled_for = time.time() - progress_at
            if is_stalled(stalled_for, restarts):
                say(f"STALL: {restarts} orchestrator restart(s) and NO progress "
                    f"for {stalled_for/60:.0f} min (progress counter stuck at "
                    f"{last_progress}) - raising attention")
                raise_attention(STALL_NOTICE)
                # Do not exit. Keep polling, so the log still shows the box
                # alive and a fix that lands later is picked up automatically;
                # raise_attention is a no-op once the marker exists.
                progress_at = time.time()
                restarts = 0

            if os.path.exists(DONE):
                say("campaign finished - watchdog exiting")
                raise_attention(DONE_NOTICE)
                break
        except Exception as exc:  # noqa: BLE001
            say(f"watchdog error: {exc}")
        time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
