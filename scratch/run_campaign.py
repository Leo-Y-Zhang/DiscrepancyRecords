"""Session-independent campaign orchestrator for a(17) = N(17,2).

Runs unattended (launched by detach.py with NO CONSOLE, so no console control
event can reach it - see the header of detach.py for why that, and not WMI
reparenting, is what makes it session-independent). Every phase is resumable;
re-running this script continues where it left off. All output to campaign.log
next to this file.

Phases:
  1. Totalizer certified wave  (wave274tot, 16 workers, 2400 s/cube, DRAT)
     with the gzip compress-and-clear loop alongside.
  2. Timeout retries, two rounds (7200 s budget, 8 workers).
  3. DRAT check pass over the declared proof sample (drat-trim, 14 workers,
     sole owner of the checking job) - fail fast before spending days on the
     confirmation wave.
  4. Seqcount confirmation wave (wave274, 14 workers, 3600 s, verdict-only).
  5. Confirmation timeout retries.
  6. DONE.json summary.

A SAT verdict in any wave leaves rc=10 in a verdict file; phase summaries
count them - a nonzero SAT count HALTS the campaign (witness candidate: the
next session must verify it with the evaluator and rethink).
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verdict_io  # noqa: E402
from console_immunity import ignore_console_signals  # noqa: E402

# All four deaths of this campaign were a console Ctrl+C reaching the
# orchestrator: campaign.log carries a literal ^C in front of every restart
# banner, and the 15 kissat workers found dead on 20 Aug had each exited with
# STATUS_CONTROL_C_EXIT. detach.py removes the console; this ignores the signal
# even if one is acquired by some other route, and the flag is INHERITED by the
# kissat children, which is where the 15 deaths actually landed.
ignore_console_signals()

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.expandvars(
    r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe")
LOG = os.path.join(HERE, "campaign.log")
STATE = os.path.join(HERE, "campaign_state.json")

# ⚠⚠ THE FLAG BELOW IS LOAD-BEARING AND ITS ABSENCE COST THE 20:10:05 DEATH.
#
# detach.py gives THIS process no console, which is what makes it unreachable.
# The Win32 consequence one level down is the opposite of what it sounds like:
# when a CONSOLE application is started by a parent that has NO console and no
# console flag, the system allocates it A BRAND NEW CONSOLE WITH A VISIBLE
# WINDOW. So detaching the orchestrator is precisely what put its children on a
# closeable window:
#
#   orchestrator (no console)
#     -> cube_wave2.py wave      NEW CONSOLE, VISIBLE WINDOW (measured: hwnd
#          -> 17 pool workers     525522, IsWindowVisible True, 2026-08-20)
#               -> 16 kissat.exe  all inheriting that one console
#
# 34 processes behind one X button, and console_immunity's ignore-Ctrl+C flag
# does not cover CTRL_CLOSE. On 2026-08-20 the wave driver and its whole tree
# exited 3221225786 = STATUS_CONTROL_C_EXIT at 20:10:05, then the two retry
# rounds died the same way 13 and 18 seconds later, and the orchestrator - which
# survived, being consoleless - ran them into "HALT: only 5759/16384 UNSAT".
# ⚠ verify_detached.py said PASS throughout, because it only ever looked at the
# four supervisors and never at the process doing the work.
#
# CREATE_NO_WINDOW keeps ONE console for the whole subtree but gives it no
# window, so there is nothing to click and nothing for a closing terminal to
# reach. Proven both ways in test_no_console_window.py, grandchildren included:
# the old shape's child AND grandchild share a visible window and one event
# kills them both; the new shape's child and grandchild share a single
# windowless console. DETACHED_PROCESS here would be worse, not better - every
# grandchild would then get a visible window of its own, seventeen of them.
CREATE_NO_WINDOW = 0x08000000


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)


def counts(wavedir):
    # verdict_io, not bare json.load: on 2026-08-22 two zero-byte verdict
    # files (a kill caught solve_cube between open() and json.dump) made this
    # exact line raise, and the orchestrator crash-looped through 8 watchdog
    # restarts while the watchdog logged rc 0 every time. An unreadable
    # verdict is NOT counted (never as UNSAT - that would fake completion);
    # the driver just re-solves that cube.
    c = verdict_io.count_verdicts(os.path.join(HERE, wavedir, "verdicts"))
    if c.pop("unreadable", 0):
        log(f"note: unreadable verdict file(s) in {wavedir} ignored; "
            f"those cubes will be re-solved")
    return c


def run(args, tag):
    log(f"start {tag}: {' '.join(args)}")
    r = subprocess.run(args, cwd=HERE, creationflags=CREATE_NO_WINDOW)
    log(f"end {tag}: rc={r.returncode}")
    if r.returncode in (3221225786, -1073741510):
        # Name it in the log rather than leaving a bare number. Every previous
        # instance of this code was diagnosed twice, wrongly the first time.
        log(f"note: rc={r.returncode} is STATUS_CONTROL_C_EXIT - {tag} was "
            f"killed by a console control event, not by anything mathematical")
    return r.returncode


def sat_guard(wavedir, tag):
    c = counts(wavedir)
    log(f"{tag} verdicts: UNSAT={c['20']} SAT={c['10']} other={c['other']}")
    if c["10"] > 0:
        log(f"HALT: {c['10']} SAT verdict(s) in {wavedir} - witness "
            f"candidate; a session must verify with the evaluator")
        sys.exit(3)
    return c


def main():
    log("=== campaign orchestrator starting ===")
    total_tot, total_seq = 16384, 4096

    # Phase 1: totalizer wave + gzip loop
    c = counts("wave274tot")
    if c["20"] < total_tot:
        # gzip_loop.py is NOT started any more. It compressed proofs the
        # pruner was about to delete seconds later - pure waste - and the two
        # loops raced: whichever saw a finished cube first won, and when the
        # pruner won, gzip_loop never deleted that cube's .cnf, which is how
        # 462 stranded CNFs (7.2 GB) accumulated. sample_prune.py now deletes
        # the .cnf itself, so there is exactly one owner of that job.
        run([PY, os.path.join(HERE, "cube_wave2.py"), "wave", "wave274tot",
             "16", "2400"], "totalizer-wave")
        sat_guard("wave274tot", "totalizer-wave")
        # Phase 2: retries for cubes that hit the 2400s cap
        for rnd in (1, 2):
            c = counts("wave274tot")
            if c["other"] == 0:
                break
            log(f"retry round {rnd}: {c['other']} unresolved cubes at 7200s")
            run([PY, os.path.join(HERE, "cube_wave2.py"), "wave",
                 "wave274tot", "8", "7200"], f"totalizer-retry-{rnd}")
            sat_guard("wave274tot", f"totalizer-retry-{rnd}")
    c = sat_guard("wave274tot", "totalizer-final")
    if c["20"] < total_tot:
        log(f"HALT: only {c['20']}/{total_tot} UNSAT after retries - "
            f"hard cubes remain; a session must decide the budget")
        sys.exit(4)
    log("PHASE 1+2 COMPLETE: all 16384 totalizer cubes UNSAT")

    # Phase 3: DRAT check pass (fail fast before the confirmation wave).
    #
    # The front-loaded checker (sample_prune.py ... check) is STILL RUNNING
    # here - the watchdog restarts it every five minutes - and it draws from
    # the same sampled cube ids. Ask it to stand down and tell the watchdog to
    # leave it down, or two checkers duplicate each other's multi-hour proofs
    # and race over the same scratch files.
    stop = os.path.join(HERE, "wave274tot", "STOP_CHECKER")
    open(stop, "w", encoding="ascii").write("campaign phase 3 owns the checker")
    marker = os.path.join(HERE, "PHASE3_ACTIVE")
    open(marker, "w", encoding="ascii").write(time.strftime("%Y-%m-%d %H:%M:%S"))
    lock = os.path.join(HERE, "wave274tot", "PRUNER_RUNNING_check")
    waited = 0
    while (os.path.exists(lock)
           and time.time() - os.path.getmtime(lock) < 1800 and waited < 5400):
        log(f"waiting for the front-loaded checker to stand down ({waited}s)")
        time.sleep(300)
        waited += 300

    # 14 of 16 threads, not 8. The wave is finished by now and the box is
    # otherwise idle, so 8 left half the machine doing nothing for the longest
    # single phase of the campaign. Measured 2026-08-20: drat-trim peaks at
    # ~150 MB resident, so 14 of them is ~2 GB against the ~10 GB the sixteen
    # kissats have just released - memory is not the limit here.
    rc = run([PY, os.path.join(HERE, "check_pass.py"), "wave274tot", "14"],
             "drat-check-pass")
    for f in (stop, marker):
        if os.path.exists(f):
            os.remove(f)
    if rc == 3:
        # Distinct from a failed proof ON PURPOSE. rc 3 means some cubes were
        # never checked because workers died - an environment fault. Calling
        # that "a proof did not verify" is how a bad afternoon gets mistaken
        # for a mathematical result.
        log("HALT: check pass could not check every sampled proof "
            "(CHECKER_ERRORS.jsonl) - ENVIRONMENT FAULT, not a bad proof")
        sys.exit(7)
    if rc != 0:
        log("HALT: check pass reported failures - do NOT proceed")
        sys.exit(5)
    log("PHASE 3 COMPLETE: every cube proof drat-trim s VERIFIED")

    # Phase 4+5: seqcount confirmation wave (verdict-only)
    c = counts("wave274")
    if c["20"] < total_seq:
        run([PY, os.path.join(HERE, "cube_wave2.py"), "wave", "wave274",
             "14", "3600", "--no-proof"], "seqcount-wave")
        sat_guard("wave274", "seqcount-wave")
        for rnd in (1, 2):
            c = counts("wave274")
            if c["other"] == 0:
                break
            log(f"seqcount retry {rnd}: {c['other']} unresolved at 7200s")
            run([PY, os.path.join(HERE, "cube_wave2.py"), "wave", "wave274",
                 "8", "7200", "--no-proof"], f"seqcount-retry-{rnd}")
            sat_guard("wave274", f"seqcount-retry-{rnd}")
    c = sat_guard("wave274", "seqcount-final")
    if c["20"] < total_seq:
        log(f"HALT: only {c['20']}/{total_seq} seqcount cubes UNSAT")
        sys.exit(6)
    log("PHASE 4+5 COMPLETE: all 4096 seqcount cubes UNSAT")

    with open(os.path.join(HERE, "DONE.json"), "w", encoding="ascii",
              newline="\n") as fh:
        json.dump({"finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime()),
                   "totalizer": counts("wave274tot"),
                   "seqcount": counts("wave274")}, fh, indent=1)
    log("=== CAMPAIGN COMPUTE COMPLETE - N(17,2) = 274 both-encoder "
        "verified, all totalizer proofs drat-trim checked. A session now "
        "imports the evidence and assembles the claim. ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
