"""Verify cube proofs as the wave produces them, then reclaim their disk.

Disk arithmetic that forced this: ~16 MB of gzipped DRAT per cube, 16,384
cubes, so hoarding every proof to the end needs ~260 GB and the run would
race the free space. Verifying continuously bounds disk AND front-loads the
check pass, which would otherwise cost hours after the wave.

For each cube whose proof is compressed and whose verdict says rc=20:
  decompress -> confirm the bytes hash to the digest the solver recorded ->
  drat-trim -> on `s VERIFIED` append a transcript line and DELETE the proof.

What survives is the transcript: instance sha256, proof sha256, proof size,
checker and its verdict. The proof itself is regenerable (deterministic
instance + solver), so deleting it costs reproducibility nothing an
independent party could not redo - but a SAMPLE is kept (every 512th cube)
so a reader can spot-check a real artifact without regenerating anything.

Any proof that does not verify is KEPT, recorded in CHECK_FAILURES.jsonl,
and stops this loop: that is a result, not a nuisance.

Usage: check_and_prune.py <wavedir> <total_cubes> <workers>
"""
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import verdict_io

HERE = os.path.dirname(os.path.abspath(__file__))
DRATTRIM = r"C:\dev\Tools\drat-trim\drat-trim-rebuilt.exe"
KEEP_EVERY = 512

# 0xC000013A = STATUS_CONTROL_C_EXIT: what Windows leaves behind when a process
# is taken down by a console control event. It is the exit code every death in
# this campaign has carried, and on 2026-08-20 the checker's drat-trim children
# were measured sitting on a VISIBLE console window of their own - reachable by
# one click, because a console application whose parent has no console is given
# a brand new console, with a window, by the system.
#
# Without this, such a kill is indistinguishable from a bad proof: drat-trim
# dies before printing anything, no `s VERIFIED` line is found, ok is False,
# and the row lands in CHECK_FAILURES.jsonl - which HALTS THE CAMPAIGN and
# tells the operator that a proof did not verify. After days of compute, a
# closed window would read as the mathematics failing. So this one case is
# raised as an infrastructure fault instead, which the callers already route to
# CHECKER_ERRORS.jsonl, leaving the cube unchecked for the next pass.
# ⚠ ONLY a control-C exit with no verdict qualifies. A drat-trim that runs and
# says `s NOT VERIFIED` is a genuine result and must still stop everything.
CONSOLE_KILL_RCS = frozenset({3221225786, -1073741510})


class CheckerKilled(RuntimeError):
    """Killed from outside mid-check. An environment fault, never a proof result."""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scratch_paths(w, cube):
    """Per-PROCESS scratch names. Never key these on the cube id alone.

    Campaign phase 3 runs check_pass.py while the front-loaded sample checker
    is still alive (the watchdog restarts it every five minutes, and the guard
    that was supposed to prevent the overlap waits on a lock file nothing
    creates). Both then pick from the same sampled cube ids. With the id as the
    only key, both build `chk_<cube>.cnf` and `chk_<cube>.drat` at the same two
    paths and each one's `finally` deletes the other's input from under a
    running drat-trim.

    OBSERVED 2026-08-20 with 8 concurrent checkers on one cube: 4 returned
    `proof sha != recorded` and 3 died with PermissionError out of the cleanup.
    Every one of those is written to CHECK_FAILURES.jsonl, which halts the
    campaign and raises CAMPAIGN_ATTENTION - so after four days of compute a
    filename clash would read as the mathematics failing.
    """
    tag = f"{cube:05d}_{os.getpid()}"
    return os.path.join(w, f"chk_{tag}.cnf"), os.path.join(w, f"chk_{tag}.drat")


def _discard(*paths):
    """Cleanup must never raise. An exception escaping `finally` replaces the
    real result - good or bad - with an OSError the caller reads as a failed
    proof, and it propagates out of fut.result() and kills the whole pass."""
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def check_one(args):
    wavedir, cube, lits, want_sha = args
    w = os.path.join(HERE, wavedir)
    gz = os.path.join(w, "drat", f"cube_{cube:05d}.drat.gz")
    raw = os.path.join(w, "drat", f"cube_{cube:05d}.drat")
    tmp_cnf, tmp_drat = scratch_paths(w, cube)
    try:
        if not os.path.exists(gz) and not os.path.exists(raw):
            return {"cube": cube, "ok": False, "error": "proof missing"}
        with open(os.path.join(w, "base.cnf"), encoding="ascii") as fh:
            header = fh.readline().split()
            body = fh.read()
        with open(tmp_cnf, "w", encoding="ascii", newline="\n") as fh:
            fh.write(f"p cnf {header[2]} {int(header[3]) + len(lits)}\n")
            fh.write(body)
            for x in lits:
                fh.write(f"{x} 0\n")
        if os.path.exists(gz):
            with gzip.open(gz, "rb") as fin, open(tmp_drat, "wb") as fout:
                shutil.copyfileobj(fin, fout, 1 << 20)
        else:
            shutil.copyfile(raw, tmp_drat)
        got = sha256_file(tmp_drat)
        if got != want_sha:
            return {"cube": cube, "ok": False,
                    "error": f"proof sha {got[:12]} != recorded {want_sha[:12]}"}
        t0 = time.time()
        r = subprocess.run([DRATTRIM, tmp_cnf, tmp_drat], capture_output=True,
                           text=True, timeout=5400)
        lines = [ln for ln in r.stdout.splitlines() if ln.startswith("s ")]
        verdict = lines[-1] if lines else ""
        ok = verdict == "s VERIFIED"
        if not lines and r.returncode in CONSOLE_KILL_RCS:
            raise CheckerKilled(
                f"drat-trim on cube {cube} was killed by a console control "
                f"event (rc={r.returncode}) and produced no verdict - "
                f"infrastructure, not a proof failure")
        row = {"cube": cube, "ok": ok, "tool": "drat-trim", "tool_rc": r.returncode,
               "verdict": verdict, "drat_sha256": want_sha,
               "drat_bytes": os.path.getsize(tmp_drat),
               "cnf_sha256": sha256_file(tmp_cnf),
               "check_wall_s": round(time.time() - t0, 1)}
        if ok and cube % KEEP_EVERY != 0:
            for victim in (gz, raw):
                if os.path.exists(victim):
                    try:
                        os.remove(victim)
                    except OSError:
                        pass
            row["proof_pruned"] = True
        else:
            row["proof_pruned"] = False
        return row
    except subprocess.TimeoutExpired:
        return {"cube": cube, "ok": False, "error": "drat-trim timeout 5400s"}
    except CheckerKilled:
        # Must be re-raised BEFORE the blanket handler below, which would turn
        # it into an ok:False row and put it straight into CHECK_FAILURES -
        # exactly the false "the mathematics failed" this exists to prevent.
        raise
    except Exception as e:  # noqa: BLE001 - a checker must not die on one cube
        return {"cube": cube, "ok": False, "error": repr(e)}
    finally:
        _discard(tmp_cnf, tmp_drat)


def load_done(trans):
    done = set()
    if os.path.exists(trans):
        with open(trans, encoding="ascii") as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["cube"])
                except Exception:  # noqa: BLE001
                    pass
    return done


def _main():
    wavedir, total, workers = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    w = os.path.join(HERE, wavedir)
    verd, drat = os.path.join(w, "verdicts"), os.path.join(w, "drat")
    trans = os.path.join(w, "transcripts.jsonl")
    fails = os.path.join(w, "CHECK_FAILURES.jsonl")
    stop = os.path.join(w, "STOP_CHECKER")
    lock = os.path.join(w, "PRUNER_RUNNING")
    open(lock, "w", encoding="ascii").write(str(os.getpid()))
    done = load_done(trans)
    print(f"prune-checker starting: {len(done)}/{total} already transcribed",
          flush=True)
    nfail = 0
    while len(done) < total and not os.path.exists(stop):
        os.utime(lock, None)
        ready = []
        for f in os.listdir(drat):
            if not f.endswith(".drat.gz"):
                continue
            cube = int(f[len("cube_"):-len(".drat.gz")])
            if cube in done:
                continue
            # Unreadable verdict => cube not solved => nothing to check yet.
            # A bare json.load here died on a zero-byte file, 2026-08-22.
            v = verdict_io.read_verdict(os.path.join(verd, f"v{cube:05d}.json"))
            if v is None:
                continue
            if v.get("rc") == 20 and v.get("drat_sha256"):
                ready.append((wavedir, cube, v["lits"], v["drat_sha256"]))
        if not ready:
            time.sleep(120)
            continue
        ready.sort(key=lambda t: t[1])
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(check_one, j) for j in ready[:400]]
            for fut in as_completed(futs):
                row = fut.result()
                done.add(row["cube"])
                if row["ok"]:
                    with open(trans, "a", encoding="ascii", newline="\n") as fh:
                        fh.write(json.dumps(row) + "\n")
                else:
                    nfail += 1
                    with open(fails, "a", encoding="ascii", newline="\n") as fh:
                        fh.write(json.dumps(row) + "\n")
                    print(f"!! CHECK FAILURE cube {row['cube']}: "
                          f"{row.get('error') or row.get('verdict')}", flush=True)
                if len(done) % 250 == 0:
                    print(f"prune-checker: {len(done)}/{total} transcribed, "
                          f"{nfail} failures", flush=True)
        if nfail:
            print(f"prune-checker: HALTING with {nfail} failure(s) - proofs "
                  f"kept for inspection", flush=True)
            return 1
    print(f"prune-checker done: {len(done)}/{total}, {nfail} failures",
          flush=True)
    return 0 if nfail == 0 else 1


def main():
    try:
        return _main()
    finally:
        lk = os.path.join(HERE, sys.argv[1], "PRUNER_RUNNING")
        if os.path.exists(lk):
            os.remove(lk)


if __name__ == "__main__":
    raise SystemExit(main())
