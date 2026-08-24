"""Launch a long-running campaign process that NOTHING in a terminal can kill.

WHY THIS EXISTS - the campaign died four times and every death was the same one.
`campaign.log` records it in plain sight: every "=== campaign orchestrator
starting ===" line is prefixed with a literal ^C written by the cmd.exe wrapper
of the run that died just before it.

    ^C[2026-08-19 22:57:07] === campaign orchestrator starting ===
    ^C[2026-08-20 03:11:04] === campaign orchestrator starting ===
    ^C[2026-08-20 17:56:12] === campaign orchestrator starting ===
    ^C[2026-08-20 19:18:33] === campaign orchestrator starting ===

^C is what cmd.exe prints when a CTRL_C_EVENT reaches it. So the killer was
never a logoff and never a crash: it was a CONSOLE CONTROL EVENT, delivered to
the orchestrator every time a terminal went away. The 19 Aug diagnosis of "a
logoff killed it" fitted that one instance and was wrong as a general rule -
the 20 Aug 19:15 death happened with explorer.exe running unbroken since 17:55,
i.e. with no logoff at all.

WMI creation was not the protection it was believed to be. It reparents the
process (WmiPrvSE, not the terminal) so a TREE kill cannot reach it, but the
`cmd.exe /c ... >> log` wrapper it was given still ALLOCATES A CONSOLE, and a
console is a shared object that console control events are broadcast across.
Escaping the process tree does not escape the console.

THE FIX, and it is structural rather than defensive: give the process no
console to be signalled through.

  DETACHED_PROCESS           the child gets NO console. A console control event
                             has nowhere to be delivered. This is the one that
                             actually closes the hole.
  CREATE_NEW_PROCESS_GROUP   nothing can address it as part of a group.
  CREATE_BREAKAWAY_FROM_JOB  a Job object with KILL_ON_JOB_CLOSE (which is how
                             terminals commonly clean up) cannot take it down.
                             Not every job permits breakaway, so this one is
                             attempted and dropped if refused - the other two
                             flags are the load-bearing ones.

stdout and stderr go straight to an inherited file handle, which is what the
cmd.exe wrapper was there for in the first place. Removing that wrapper removes
the console with it.

⚠⚠ DETACHING IS NOT FREE ONE LEVEL DOWN, and that cost went unnoticed for two
hours on 2026-08-20. A CONSOLE application started by a parent that has NO
console, with no console flag of its own, is given A BRAND NEW CONSOLE WITH A
VISIBLE WINDOW by the system. So a detached parent that spawns console children
hands each of them a window a person can close - and CTRL_CLOSE is the one
console event that SetConsoleCtrlHandler(NULL, TRUE) does not suppress.

That is why --no-window exists. Processes here fall into two kinds:

  no children, or only CREATE_NO_WINDOW children  -> DETACHED_PROCESS (default)
     watchdog.py. Unattachable: strictly the strongest option, and free.

  spawns console children it cannot pass flags to  -> --no-window
     sample_prune.py, whose ProcessPoolExecutor workers are created by
     multiprocessing with creationflags hardcoded to 0. The only way those
     workers get no window is to INHERIT a console that has none, which means
     the parent must own one. Measured 2026-08-20: with the parent detached,
     the checker's pool worker owned its own conhost and drat-trim ran on it.

  ⚠ The trade is real and is not hidden: a --no-window process CAN be attached
  to, so a process that deliberately calls AttachConsole can still signal it.
  Nothing on this box does that. A window, by contrast, has been clicked.

Usage:  detach.py [--no-window] <logfile> <pidfile> <exe> [args...]
Prints the child PID and, unless pidfile is "-", writes it there. The pidfile
exists because the caller is normally WMI (which gives no way to read stdout)
and because the PID that matters is the CHILD's - reporting the launcher's PID
in watchdog.log would name a process that has already exited.
"""
import os
import subprocess
import sys

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000

HERE = os.path.dirname(os.path.abspath(__file__))


def spawn(logfile, argv, no_window=False):
    """Start argv detached, appending output to logfile. Returns the PID.

    no_window=True swaps DETACHED_PROCESS for CREATE_NO_WINDOW: the child keeps
    ONE console, with no window, and everything it spawns inherits that console
    instead of being handed a fresh visible one. See the module header.
    """
    # Opened append-binary so two writers interleave by line the way the old
    # `>>` redirection did, rather than one truncating the other.
    handle = open(logfile, "ab")
    base = (CREATE_NO_WINDOW if no_window else DETACHED_PROCESS) \
        | CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(
            argv,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=base | CREATE_BREAKAWAY_FROM_JOB,
            cwd=HERE,
            close_fds=False,
        )
    except OSError:
        # The job we are in does not allow breakaway. Losing that flag costs the
        # job-kill protection but keeps the console protection, which is the one
        # that was actually killing this campaign.
        proc = subprocess.Popen(
            argv,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=base,
            cwd=HERE,
            close_fds=False,
        )
    handle.close()
    return proc.pid


def main():
    argv = sys.argv[1:]
    no_window = False
    if argv and argv[0] == "--no-window":
        no_window, argv = True, argv[1:]
    if len(argv) < 3:
        sys.stderr.write(
            "usage: detach.py [--no-window] <logfile> <pidfile> <exe> [args...]\n")
        return 2
    logfile, pidfile = argv[0], argv[1]

    # ⚠ THIS LAUNCHER IS NORMALLY STARTED BY WMI, WHICH DISCARDS ITS STDERR.
    # The first migration run hit exactly that blind spot: two of the three
    # launches failed silently and resume_campaign.ps1 reported "PID 0" while
    # the campaign was simply not running. The cause was worth recording -
    # orphaned multiprocessing workers from the killed run had INHERITED the
    # cmd.exe `>>` handle on campaign.log, and cmd.exe opens a redirection
    # target denying write sharing, so this process could not open the log.
    # A launcher that cannot say why it failed is a launcher that fails silently
    # for five days, so failures go to a file next to the pid file.
    try:
        child = spawn(logfile, argv[2:], no_window=no_window)
    except Exception as exc:  # noqa: BLE001 - the report matters, not the type
        err = (pidfile + ".error") if pidfile != "-" else os.path.join(HERE, "detach.error")
        with open(err, "w", encoding="ascii") as fh:
            fh.write(f"{type(exc).__name__}: {exc}\nlog={logfile}\n"
                     f"no_window={no_window}\nargv={argv[2:]}\n")
        sys.stderr.write(f"detach failed: {exc}\n")
        return 1

    if pidfile != "-":
        stale = pidfile + ".error"
        if os.path.exists(stale):
            try:
                os.remove(stale)
            except OSError:
                pass
        with open(pidfile, "w", encoding="ascii") as fh:
            fh.write(str(child))
    print(child)
    return 0


if __name__ == "__main__":
    sys.exit(main())
