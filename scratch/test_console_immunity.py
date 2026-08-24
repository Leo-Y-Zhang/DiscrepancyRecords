"""Prove the console fix, rather than asserting it.

The campaign died four times to a console control event (campaign.log prefixes
every restart banner with a literal ^C; the 15 workers found dead on 20 Aug had
exited 0xC000013A = STATUS_CONTROL_C_EXIT). detach.py claims to close that hole
by giving the process no console. This test tries to KILL both shapes the same
way and reports which survives.

  canary OLD  - launched with its own console, the shape the cmd.exe wrapper
                produced. The test attaches to that console and fires a real
                CTRL_C_EVENT at it.
  canary NEW  - launched through detach.py (DETACHED_PROCESS) with
                console_immunity applied inside.

PASS means: OLD dies, and NEW cannot even be attached to - AttachConsole fails
because there is no console to attach to, so there is no route by which the
event that killed the campaign could be delivered.

A test that only checked NEW survives would be weak: it could pass because the
signal was never delivered to anything. Killing OLD with the same call is the
control that proves the signal was real.

Usage:  test_console_immunity.py            run the test
        test_console_immunity.py canary X   internal: the canary process
"""
import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CREATE_NEW_CONSOLE = 0x00000010
CTRL_C_EVENT = 0
CTRL_BREAK_EVENT = 1
ERROR_INVALID_HANDLE = 6
ERROR_GEN_FAILURE = 31

PY = sys.executable


def canary(tag):
    """Heartbeat forever. NEW applies console immunity; OLD deliberately does not."""
    if tag == "new":
        from console_immunity import ignore_console_signals

        ignore_console_signals()
    else:
        # EXPLICITLY vulnerable. The ignore-Ctrl+C flag is inherited, and this
        # process tree may already carry it from whatever launched the test -
        # an inherited flag would make the control survive and silently turn
        # the control into a second copy of the treatment.
        ctypes.WinDLL("kernel32", use_last_error=True).SetConsoleCtrlHandler(None, 0)
    path = os.path.join(HERE, f"canary_{tag}.txt")
    while True:
        with open(path, "w", encoding="ascii") as fh:
            fh.write(str(time.time()))
        time.sleep(0.25)


def alive(pid):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


def main():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # ⚠ ORDER IS LOad-BEARING AND THE FIRST VERSION OF THIS TEST GOT IT WRONG.
    # The tester has to make itself immune before firing the event, but the
    # ignore-Ctrl+C flag is INHERITED by child processes - so setting it first
    # handed the "unprotected" control canary the very protection under test,
    # and the control survived a signal it was supposed to die from. Reported
    # FAIL, which is the only reason it was caught. Spawn the canaries FIRST,
    # then arm the tester.
    print("launching OLD-shape canary (own console, no immunity)...")
    old = subprocess.Popen(
        [PY, "-u", os.path.abspath(__file__), "canary", "old"],
        creationflags=CREATE_NEW_CONSOLE,
        cwd=HERE,
    )

    print("launching NEW-shape canary (detach.py, no console + immunity)...")
    import detach

    new_pid = detach.spawn(
        os.path.join(HERE, "canary_new.log"),
        [PY, "-u", os.path.abspath(__file__), "canary", "new"],
    )

    time.sleep(3)
    if not alive(old.pid):
        print("SETUP FAILED: OLD canary never started")
        return 1
    if not alive(new_pid):
        print("SETUP FAILED: NEW canary never started")
        return 1
    print(f"both alive: OLD pid={old.pid}  NEW pid={new_pid}")

    # Only NOW - after both canaries exist, so neither inherits it.
    k32.SetConsoleCtrlHandler(None, 1)

    # --- the control: kill OLD with a real console control event -------------
    k32.FreeConsole()
    attached_old = bool(k32.AttachConsole(old.pid))
    print(f"AttachConsole(OLD={old.pid}) -> {attached_old}")
    fired = False
    killer = "none"
    if attached_old:
        fired = bool(k32.GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0))
        time.sleep(3)
        if not alive(old.pid):
            killer = "CTRL_C_EVENT"
        else:
            # Ctrl+Break is the other console control event a closing terminal
            # sends, and it is NOT suppressed by the ignore-Ctrl+C flag.
            if k32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, 0):
                fired = True
                time.sleep(3)
                if not alive(old.pid):
                    killer = "CTRL_BREAK_EVENT"
    k32.FreeConsole()

    old_dead = not alive(old.pid)

    # --- the subject: can the same event even reach NEW? ---------------------
    attached_new = bool(k32.AttachConsole(new_pid))
    err_new = ctypes.get_last_error() if not attached_new else 0
    if attached_new:
        k32.GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)
        k32.FreeConsole()

    time.sleep(3)
    new_alive = alive(new_pid)

    # Reattach to something printable, then report.
    k32.AllocConsole()
    lines = [
        "",
        "=" * 68,
        f"  console control event fired on OLD  : {fired}",
        f"  OLD canary killed, and by what      : {old_dead} ({killer})",
        f"  AttachConsole(NEW)                  : {attached_new}"
        + (f"  (error {err_new}"
           + (" = ERROR_INVALID_HANDLE, no console exists)" if err_new == ERROR_INVALID_HANDLE
              else " = ERROR_GEN_FAILURE, no console exists)" if err_new == ERROR_GEN_FAILURE
              else ")")
           if not attached_new else ""),
        f"  NEW canary still alive              : {new_alive}",
        "=" * 68,
    ]
    ok = fired and old_dead and (not attached_new) and new_alive
    lines.append("  RESULT: PASS - the killer reaches the old shape and cannot"
                 " reach the new one" if ok else
                 "  RESULT: FAIL - do not rely on this fix")
    lines.append("=" * 68)
    report = "\n".join(lines)
    print(report)
    with open(os.path.join(HERE, "console_immunity_result.txt"), "w", encoding="ascii") as fh:
        fh.write(report + "\n")

    for pid in (old.pid, new_pid):
        if alive(pid):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True)
    for tag in ("old", "new"):
        for ext in ("txt", "log"):
            p = os.path.join(HERE, f"canary_{tag}.{ext}")
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "canary":
        canary(sys.argv[2])
    else:
        sys.exit(main())
