"""Prove the SECOND console hole - the one detach.py does not close.

BACKGROUND. detach.py gives the orchestrator, the pruner, the checker and the
watchdog DETACHED_PROCESS, so those four own no console and no console control
event can reach them. That was verified and it holds. It is also only half the
story, and the half it misses is where all the compute lives.

Win32 rule: when a CONSOLE application is started by a parent that has NO
console, and no console flag is given, the system ALLOCATES A NEW CONSOLE for
the child - with a visible window. So detaching the orchestrator is what caused
its children to be born on a fresh, closeable console:

    orchestrator (no console)
      -> cube_wave2.py wave ...        NEW CONSOLE, VISIBLE WINDOW
           -> 17 pool workers          (inherit that console)
                -> 16 kissat.exe       (inherit that console)

34 processes on one window. Measured on the live campaign 2026-08-20 21:5x:
the wave driver owned window handle 525522, IsWindowVisible = True, and
verify_detached.py reported PASS the whole time because it only ever looked at
the four supervisors.

That is the 20:10:05 death: rc=3221225786 = STATUS_CONTROL_C_EXIT on the wave
driver, eight poisoned verdicts, while the four consoleless supervisors carried
on and the orchestrator ran its retries into a HALT.

WHAT THIS TEST DOES. It does not assert about flags; it kills things.
From a consoleless parent - the orchestrator's exact situation - it starts two
canaries, and each canary starts a GRANDCHILD with no flags at all:

  old shape  Popen(argv)                     what run_campaign.py used to do
  new shape  Popen(argv, CREATE_NO_WINDOW)   what it does now

The grandchild is the load-bearing half of the test. Production's pool workers
are grandchildren, and multiprocessing gives them no creation flags, so a fix
that merely moves the console allocation down one level would produce
SEVENTEEN windows instead of one and would read as a fix in a review.

PASS requires all four of:
  - the old canary owns a VISIBLE console window, and its grandchild is on the
    SAME one                                              (defect reproduced)
  - a real console control event fired at that console kills BOTH of them
    (the route is real, and its blast radius is the whole tree)
  - neither the new canary nor its grandchild owns a visible window
  - the new pair share ONE console rather than one each - a fix that merely
    moved the allocation down a level would give every pool worker its own

WHAT THIS FIX DOES NOT CLAIM, measured here rather than glossed: the windowless
console still EXISTS and AttachConsole still succeeds on it, so a process that
deliberately attaches can still fire an event at the wave. What is gone is the
route that has actually been killing this campaign - a window a person or a
tidy-up tool can close, and a terminal sharing the console. There is no API
that gives a child process a console its own children can inherit while being
unattachable; DETACHED_PROCESS is unattachable but hands every grandchild a
fresh VISIBLE window, which is strictly worse and is what the old shape proves.

Usage:  test_no_console_window.py
"""
import ctypes
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

CTRL_C_EVENT = 0
CTRL_BREAK_EVENT = 1
STILL_ACTIVE = 259
ERROR_INVALID_HANDLE = 6
ERROR_GEN_FAILURE = 31
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

NAMES = ("old", "old_gc", "new", "new_gc")
PIDS_JSON = os.path.join(HERE, "wnd_canary_pids.json")


def rec_path(name):
    return os.path.join(HERE, f"wnd_canary_{name}.json")


def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetConsoleWindow.restype = ctypes.c_void_p
    return k


def _u32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    return u


def alive(pid):
    k = _k32()
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == STILL_ACTIVE


def exit_code(pid):
    k = _k32()
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return "gone"
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return None if code.value == STILL_ACTIVE else code.value


def canary(name, grandchild):
    """Report my own console window, spawn a bare grandchild, then wait to be killed."""
    k, u = _k32(), _u32()
    # EXPLICITLY vulnerable. The ignore-Ctrl+C flag is inherited, and this tree
    # may already carry it from whatever launched the test. An inherited flag
    # would let the control survive the event it exists to die from and would
    # silently turn the control into a second copy of the treatment - a mistake
    # this campaign has already made once, in test_console_immunity.py.
    k.SetConsoleCtrlHandler(None, 0)

    hwnd = k.GetConsoleWindow()
    rec = {
        "pid": os.getpid(),
        "hwnd": int(hwnd) if hwnd else 0,
        "visible": bool(u.IsWindowVisible(ctypes.c_void_p(hwnd))) if hwnd else False,
    }
    with open(rec_path(name), "w", encoding="ascii") as fh:
        json.dump(rec, fh)

    if grandchild != "-":
        # NO creation flags, on purpose: this is what multiprocessing does to
        # spawn a pool worker, and it is the case a flag-only fix can miss.
        subprocess.Popen([PY, os.path.abspath(__file__), "canary", grandchild, "-"],
                         close_fds=False)
    time.sleep(240)
    return 0


def parent():
    """Consoleless, exactly like the orchestrator. Start one canary each way."""
    old = subprocess.Popen(
        [PY, os.path.abspath(__file__), "canary", "old", "old_gc"],
        close_fds=False)
    new = subprocess.Popen(
        [PY, os.path.abspath(__file__), "canary", "new", "new_gc"],
        creationflags=CREATE_NO_WINDOW, close_fds=False)
    with open(PIDS_JSON, "w", encoding="ascii") as fh:
        json.dump({"old": old.pid, "new": new.pid}, fh)
    return 0


def wait_for(path, timeout=45):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            try:
                with open(path, encoding="ascii") as fh:
                    return json.load(fh)
            except (ValueError, OSError):
                pass
        time.sleep(0.2)
    return None


def fire_at(pid, k):
    """Attach to that process's console and fire the killer. Returns (attached, err, killer)."""
    k.FreeConsole()
    if not k.AttachConsole(pid):
        return False, ctypes.get_last_error(), "unreachable"
    killer = "survived"
    if k.GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0):
        time.sleep(3)
        if not alive(pid):
            killer = "CTRL_C_EVENT"
    if killer == "survived" and k.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, 0):
        time.sleep(3)
        if not alive(pid):
            killer = "CTRL_BREAK_EVENT"
    k.FreeConsole()
    return True, 0, killer


def cleanup(pids):
    for pid in pids:
        if pid and alive(pid):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
    for name in NAMES:
        for p in (rec_path(name),):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    if os.path.exists(PIDS_JSON):
        try:
            os.remove(PIDS_JSON)
        except OSError:
            pass


def main():
    cleanup([])

    subprocess.run([PY, os.path.abspath(__file__), "parent"],
                   creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                   cwd=HERE)

    recs = {name: wait_for(rec_path(name)) for name in NAMES}
    missing = [n for n, r in recs.items() if not r]
    if missing:
        print(f"SETUP FAILED: no report from {missing}")
        cleanup([r["pid"] for r in recs.values() if r])
        return 2

    report = ["shape of each process, as born from a consoleless parent:"]
    for name in NAMES:
        r = recs[name]
        report.append(f"  {name:<7} pid {r['pid']:<7} hwnd {r['hwnd']:<10} "
                      f"visible={r['visible']}")

    # The tester must not arm itself until every canary exists: the
    # ignore-Ctrl+C flag is INHERITED, so arming first would hand the controls
    # the protection under test.
    k = _k32()
    handler = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)(lambda evt: 1)
    k.SetConsoleCtrlHandler(handler, 1)

    failures = []
    if not (recs["old"]["hwnd"] and recs["old"]["visible"]):
        failures.append("old shape had NO visible console window - the defect "
                        "did not reproduce, so nothing below proves anything")
    if not (recs["old"]["hwnd"] and recs["old_gc"]["hwnd"] == recs["old"]["hwnd"]):
        failures.append("old grandchild is not on its parent's console, so this "
                        "reproduction does not match production, where the pool "
                        "workers inherit the wave driver's console")

    # Fire once at the OLD console. Both processes on it must die - that is the
    # blast radius the campaign actually suffered.
    att_old, _, killer_old = fire_at(recs["old"]["pid"], k)
    time.sleep(2)
    old_dead = not alive(recs["old"]["pid"])
    gc_old_dead = not alive(recs["old_gc"]["pid"])

    # Fire once at the NEW console. The grandchild dying WITH its parent is the
    # evidence that they share one console; a grandchild that survived would
    # mean it had been given a console of its own, i.e. the allocation moved
    # down a level instead of being removed, and production would come up with
    # seventeen of them.
    att_new, err_new, killer_new = fire_at(recs["new"]["pid"], k)
    time.sleep(2)
    new_dead = not alive(recs["new"]["pid"])
    ngc_dead = not alive(recs["new_gc"]["pid"])

    k.AllocConsole()

    if not att_old:
        failures.append("could not attach to the old shape's console - the "
                        "kill route was never exercised")
    if not (old_dead and gc_old_dead):
        failures.append(f"one event did not take the whole old tree (child "
                        f"dead={old_dead}, grandchild dead={gc_old_dead}) - "
                        f"this test is not measuring what it claims")
    for key in ("new", "new_gc"):
        if recs[key]["hwnd"] or recs[key]["visible"]:
            failures.append(f"{key} owns a console WINDOW (hwnd "
                            f"{recs[key]['hwnd']}) - a click still kills it")
    if new_dead and not ngc_dead:
        failures.append("the new grandchild outlived its parent, so it holds a "
                        "console of its OWN - CREATE_NO_WINDOW moved the "
                        "allocation down a level instead of removing it")

    report += [
        "",
        f"  old    attach={att_old}  killed_by={killer_old}  dead={old_dead}",
        f"  old_gc same console as its parent, dead={gc_old_dead}",
        f"  new    attach={att_new}"
        + ("" if att_new else f" (err {err_new})")
        + f"  outcome={killer_new}  dead={new_dead}",
        f"  new_gc died with its parent = shares one console: {ngc_dead}",
        "",
    ]
    if failures:
        report.append("RESULT: FAIL")
        report += [f"  - {f}" for f in failures]
    else:
        report.append("RESULT: PASS - a consoleless parent gives its child a "
                      "VISIBLE console window shared with its")
        report.append("        grandchildren, and one event kills them all. "
                      "CREATE_NO_WINDOW keeps the single")
        report.append("        shared console but removes the window at BOTH "
                      "levels, so there is nothing to close.")
    text = "\n".join(report)
    print(text)
    with open(os.path.join(HERE, "no_console_window_result.txt"), "w",
              encoding="ascii") as fh:
        fh.write(text + "\n")

    cleanup([r["pid"] for r in recs.values()])
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "canary":
        sys.exit(canary(sys.argv[2], sys.argv[3]))
    if len(sys.argv) > 1 and sys.argv[1] == "parent":
        sys.exit(parent())
    sys.exit(main())
