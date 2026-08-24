"""Prove the LIVE campaign cannot be killed through a console - ALL of it.

WHY THIS FILE WAS REWRITTEN, 2026-08-20 22:xx. The previous version checked
four processes: orchestrator, pruner, checker, watchdog. It reported

    RESULT: PASS - nothing here can be reached by a console control event

while the wave driver, its 17 pool workers and all 16 kissat solvers were
sitting on a CONSOLE WITH A VISIBLE WINDOW - 34 processes behind one X button,
measured at hwnd 525522, IsWindowVisible True. The campaign had died that way
95 minutes earlier: rc=3221225786 = STATUS_CONTROL_C_EXIT on the wave driver at
20:10:05, two retry rounds killed the same way seconds later, and the
orchestrator - safe, being consoleless - ran them into a HALT.

▶▶ THE LESSON, and it is the second measuring-stick failure of this campaign:
A VERIFIER THAT CHECKS THE SUPERVISORS IS NOT CHECKING THE WORK. The four
processes it looked at were the four that were never in danger. Enumerate what
is actually running and check all of it, or the PASS is about the wrong set.

The cause is a Win32 rule that makes detachment backfire one level down: a
CONSOLE application whose parent has NO console, started with no console flag,
is given a brand new console WITH A WINDOW. Detaching the orchestrator is what
put its children on a closeable window. See detach.py and run_campaign.py.

WHAT PASS MEANS HERE:
  1. No campaign process owns a console WINDOW.  Nothing to click, nothing for
     a closing terminal to reach. This is the property that has actually been
     violated, five times.
  2. The orchestrator and the watchdog own no console AT ALL, so they cannot
     even be attached to. They spawn only flagged children, so this costs
     nothing and is the strongest available guarantee.
  ⚠ Deliberately NOT claimed: that the wave and the checker are unattachable.
     They own a windowless console because their pool workers must inherit one
     (multiprocessing hardcodes creationflags=0). A process that deliberately
     calls AttachConsole could still signal them. Nothing on this box does;
     a window, by contrast, has been clicked.

Run it after any restart:  verify_detached.py
"""
import ctypes
import json
import subprocess
import sys

ERROR_INVALID_HANDLE = 6
ERROR_GEN_FAILURE = 31

# Roles that must exist, and whether they must be wholly consoleless.
REQUIRED = {
    "orchestrator": True,
    "watchdog": True,
    "wave driver": False,
    "pruner": False,
    "checker": False,
}
# Roles that come and go with the phase - absence is not a failure.
OPTIONAL = ("pool worker", "kissat", "drat-trim", "check_pass")

PS = r"""
$me = $PID
Get-CimInstance Win32_Process |
  Where-Object { $_.ProcessId -ne $me -and (
      $_.Name -eq 'kissat.exe' -or $_.Name -eq 'drat-trim-rebuilt.exe' -or
      ($_.Name -eq 'python.exe' -and $_.CommandLine -ne $null)) } |
  ForEach-Object {
      [pscustomobject]@{ pid=$_.ProcessId; name=$_.Name; cmd=$_.CommandLine }
  } | ConvertTo-Json -Compress
"""


def classify(row):
    """Role of a process, or None if it is nothing to do with the campaign.

    ⚠ The querying process must already have been excluded by the WMI filter.
    A command line that CONTAINS the search string matches itself, and that
    self-match once reported a dead watchdog as healthy and four phantom
    processes as verified.
    """
    if row["name"] == "kissat.exe":
        return "kissat"
    if row["name"] == "drat-trim-rebuilt.exe":
        return "drat-trim"
    cmd = row.get("cmd") or ""
    for needle, label in (
        ("run_campaign.py", "orchestrator"),
        ("cube_wave2.py", "wave driver"),
        ("watchdog.py", "watchdog"),
        ("check_pass.py", "check_pass"),
        ("spawn_main", "pool worker"),
    ):
        if needle in cmd:
            return label
    if "sample_prune.py" in cmd:
        return "checker" if " check" in cmd else "pruner"
    return None


def enumerate_campaign():
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", PS],
        capture_output=True, text=True,
    ).stdout.strip()
    if not out:
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    rows = []
    for r in data:
        role = classify(r)
        if role:
            rows.append((role, r["pid"]))
    return sorted(rows)


def probe(pid, k32, u32):
    """(has_console, hwnd, visible) for another process."""
    k32.FreeConsole()
    if not k32.AttachConsole(pid):
        err = ctypes.get_last_error()
        # ⚠ Only 6/31 mean "no console exists". ERROR_ACCESS_DENIED (5) means
        # the call was refused and proves nothing; an earlier version counted
        # it as a pass and certified four processes it had never reached.
        if err in (ERROR_INVALID_HANDLE, ERROR_GEN_FAILURE):
            return False, 0, False
        return None, 0, False
    hwnd = k32.GetConsoleWindow()
    visible = bool(u32.IsWindowVisible(ctypes.c_void_p(hwnd))) if hwnd else False
    k32.FreeConsole()
    return True, int(hwnd) if hwnd else 0, visible


def main():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32.GetConsoleWindow.restype = ctypes.c_void_p
    u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    k32.SetConsoleCtrlHandler(None, 1)

    rows = enumerate_campaign()
    seen, windows, inconclusive, consoled = {}, [], [], {}
    for role, pid in rows:
        has, hwnd, visible = probe(pid, k32, u32)
        seen.setdefault(role, []).append(pid)
        if has is None:
            inconclusive.append((role, pid))
        elif has:
            consoled.setdefault(role, []).append(pid)
            if hwnd:
                windows.append((role, pid, hwnd, visible))

    k32.AllocConsole()

    print("live campaign processes:")
    for role in sorted(seen):
        pids = seen[role]
        n_console = len(consoled.get(role, []))
        state = ("no console" if n_console == 0
                 else f"{n_console} on a console" if n_console != len(pids)
                 else "console, no window")
        if any(r == role for r, _, _, _ in windows):
            state = "*** CONSOLE WINDOW ***"
        print(f"  {role:<14} {len(pids):>3}  {state}")

    failures = []
    for role, consoleless_required in REQUIRED.items():
        if role not in seen:
            failures.append(f"{role} IS NOT RUNNING")
        elif consoleless_required and consoled.get(role):
            failures.append(f"{role} owns a console (pids {consoled[role]}) - it "
                            f"spawns only flagged children and must stay "
                            f"unattachable")
    for role, pid, hwnd, visible in windows:
        failures.append(f"{role} pid {pid} owns a console WINDOW (hwnd {hwnd}, "
                        f"visible={visible}) - one click kills it and "
                        f"everything sharing that console")
    for role, pid in inconclusive:
        failures.append(f"{role} pid {pid} INCONCLUSIVE - AttachConsole was "
                        f"refused, which is not evidence of anything")

    print()
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("RESULT: PASS - no campaign process owns a console window; the "
          "orchestrator and")
    print("        watchdog own no console at all. The wave and the checker "
          "hold one")
    print("        windowless console each, by design, so their pool workers "
          "inherit it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
