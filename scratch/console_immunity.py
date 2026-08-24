"""Make this process (and everything it spawns) ignore console control events.

Belt-and-braces beside detach.py. detach.py removes the console entirely, which
is the structural fix; this is the defensive one, and it matters because the
campaign spawns 16 kissat children and a process can acquire a console by
routes its author did not choose.

SetConsoleCtrlHandler(NULL, TRUE) sets the "ignore Ctrl+C" flag on the calling
process. Two properties make it the right call here:

  - it is INHERITED by child processes, so the kissat workers are covered
    without touching the solver invocation;
  - it survives the parent exiting, so a re-parented worker keeps the flag.

The 15 kissat workers found dead on 2026-08-20 had exited with
0xC000013A = STATUS_CONTROL_C_EXIT, which is exactly what this prevents.

CTRL_CLOSE_EVENT (the console window being closed) is NOT suppressed by this
flag - only detach.py's DETACHED_PROCESS covers that one. Hence both.
"""
import ctypes
import signal
import sys


def ignore_console_signals():
    """Ignore Ctrl+C in this process and in every child it spawns.

    Returns True if the Win32 flag was set, False on any non-Windows platform
    or if the call failed. Never raises - a hardening measure must not be the
    thing that stops the campaign.
    """
    ok = False
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, ctypes.c_int]
            kernel32.SetConsoleCtrlHandler.restype = ctypes.c_int
            ok = bool(kernel32.SetConsoleCtrlHandler(None, 1))
        except Exception:
            ok = False
    try:
        # Covers the Python-level path: without this a delivered SIGINT would
        # still raise KeyboardInterrupt out of whatever was running.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except Exception:
        pass
    return ok
