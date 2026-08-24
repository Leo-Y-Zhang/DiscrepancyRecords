"""A killed checker must never be recorded as a proof that did not verify.

CHECK_FAILURES.jsonl is the campaign's halt signal. One line in it stops the
wave, stops the watchdog restarting anything, and raises CAMPAIGN_ATTENTION on
the operator's screen saying a proof did not verify. It has to mean exactly
that, because after days of compute nobody can tell the difference by looking.

The exposure measured on 2026-08-20: the checker's drat-trim child was running
on a console with a VISIBLE WINDOW (hwnd 853348) because a console application
whose parent has no console is given a fresh one by the system. Close that
window and drat-trim dies with 0xC000013A before printing anything - no
`s VERIFIED`, so ok is False, so CHECK_FAILURES, so "the mathematics failed".

The guard must be NARROW. "Produced no verdict" is not enough on its own: a
corrupted proof is a real result, and preflight_phase3.py case B depends on one
landing in CHECK_FAILURES. Only a control-C exit code with no verdict is
infrastructure.

Usage:  test_checker_kill_classification.py            run the four cases
        test_checker_kill_classification.py --mutate   remove the guard and
                                                       watch case A fail
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import check_and_prune as cap  # noqa: E402

WAVEDIR = "_t_killtest"
CUBE = 1


class FakeCompleted:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def build_wave():
    w = os.path.join(HERE, WAVEDIR)
    shutil.rmtree(w, ignore_errors=True)
    os.makedirs(os.path.join(w, "drat"))
    with open(os.path.join(w, "base.cnf"), "w", encoding="ascii",
              newline="\n") as fh:
        fh.write("p cnf 2 1\n1 2 0\n")
    proof = os.path.join(w, "drat", f"cube_{CUBE:05d}.drat")
    with open(proof, "w", encoding="ascii", newline="\n") as fh:
        fh.write("0\n")
    h = hashlib.sha256()
    with open(proof, "rb") as fh:
        h.update(fh.read())
    return w, h.hexdigest()


def run_case(want_sha, returncode, stdout):
    """Returns ('raised', exc) or ('row', row)."""
    real_run = cap.subprocess.run
    cap.subprocess.run = lambda *a, **k: FakeCompleted(returncode, stdout)
    try:
        row = cap.check_one((WAVEDIR, CUBE, ["1"], want_sha))
        return "row", row
    except cap.CheckerKilled as exc:
        return "raised", exc
    finally:
        cap.subprocess.run = real_run


def main(mutate=False):
    if mutate:
        # The mutation: forget that a control-C exit is special. Case A must
        # fail. A test that passes under this is not testing the guard.
        cap.CONSOLE_KILL_RCS = frozenset()

    w, sha = build_wave()
    failures = []
    try:
        kind, res = run_case(sha, 3221225786, "")
        print(f"A killed by console event   -> {kind}")
        if kind != "raised":
            failures.append(
                f"A: a drat-trim killed by a console control event was "
                f"recorded as a proof result (ok={res.get('ok')}, "
                f"verdict={res.get('verdict')!r}) - that row goes to "
                f"CHECK_FAILURES and halts the campaign")

        kind, res = run_case(sha, 0, "c stuff\ns NOT VERIFIED\n")
        print(f"B genuine s NOT VERIFIED    -> {kind} ok={res.get('ok') if kind == 'row' else '-'}")
        if kind != "row" or res.get("ok"):
            failures.append("B: a real 's NOT VERIFIED' must stay a proof "
                            "failure and halt everything")

        kind, res = run_case(sha, 1, "")
        print(f"C died rc=1, no verdict     -> {kind} ok={res.get('ok') if kind == 'row' else '-'}")
        if kind != "row" or res.get("ok"):
            failures.append("C: the guard is too WIDE - a plain crash with no "
                            "verdict must still count as a failure, or a "
                            "corrupted proof could never halt the campaign")

        kind, res = run_case(sha, 0, "s VERIFIED\n")
        print(f"D s VERIFIED                -> {kind} ok={res.get('ok') if kind == 'row' else '-'}")
        if kind != "row" or not res.get("ok"):
            failures.append("D: a verified proof was not recorded as verified")
    finally:
        shutil.rmtree(w, ignore_errors=True)

    print()
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("RESULT: PASS - only a control-C exit with no verdict is treated as "
          "infrastructure;")
    print("        a real NOT VERIFIED, a plain crash and a clean verify are "
          "all unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main(mutate="--mutate" in sys.argv))
