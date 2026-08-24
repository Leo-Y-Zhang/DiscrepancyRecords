"""Post-wave DRAT verification pass: for every UNSAT cube verdict with a
gzipped proof, regenerate the cube CNF, decompress the proof, confirm the
decompressed bytes hash to the verdict's recorded drat_sha256, check with
drat-trim, append one transcript line, delete the temporaries (keep the .gz).

Resumable: cubes already in transcripts.jsonl are skipped. Failures append to
CHECK_FAILURES.jsonl and never stop the pass unless they exceed 10.

Usage: check_pass.py <wavedir> <workers>
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DRATTRIM = r"C:\dev\Tools\drat-trim\drat-trim-rebuilt.exe"

# One owner for the scratch-file naming. This module used to build
# chk_<cube>.cnf / chk_<cube>.drat itself, byte-for-byte the same two paths as
# check_and_prune.check_one, which the front-loaded sample checker runs at the
# same time - so the two raced and destroyed each other's inputs. Importing the
# names means a future change cannot fix one copy and leave the other.
import verdict_io  # noqa: E402
from check_and_prune import (  # noqa: E402
    CONSOLE_KILL_RCS,  # noqa: E402
    CheckerKilled,
    _discard,
    scratch_paths,
)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_one(args):
    wavedir, i, lits, want_sha = args
    w = os.path.join(HERE, wavedir)
    base = os.path.join(w, "base.cnf")
    # The gzip loop was retired mid-campaign, so proofs now land raw and only
    # the early ones are .gz. Reading just .gz here would have failed EVERY
    # sampled proof with "proof gz missing", tripped the >10-failure abort, and
    # halted the campaign with a check-failure reason after three days of
    # compute - a false negative dressed as a result.
    gz = os.path.join(w, "drat", f"cube_{i:05d}.drat.gz")
    raw = os.path.join(w, "drat", f"cube_{i:05d}.drat")
    if not os.path.exists(gz) and not os.path.exists(raw):
        return {"cube": i, "ok": False, "error": "proof missing"}
    tmp_cnf, tmp_drat = scratch_paths(w, i)
    try:
        with open(base, encoding="ascii") as fh:
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
        got_sha = sha256_file(tmp_drat)
        if got_sha != want_sha:
            return {"cube": i, "ok": False,
                    "error": f"sha mismatch {got_sha[:12]} vs {want_sha[:12]}"}
        t0 = time.time()
        r = subprocess.run([DRATTRIM, tmp_cnf, tmp_drat], capture_output=True,
                           text=True, timeout=3600)
        s_lines = [l for l in r.stdout.splitlines() if l.startswith("s ")]
        verdict = s_lines[-1] if s_lines else ""
        if not s_lines and r.returncode in CONSOLE_KILL_RCS:
            # Killed from outside before it could judge anything. Without this
            # it becomes an ok:False row in CHECK_FAILURES, i.e. "a proof did
            # not verify", which halts the campaign. Same guard, same reason,
            # as check_and_prune.check_one - and imported from there so the two
            # copies cannot drift.
            raise CheckerKilled(
                f"drat-trim on cube {i} was killed by a console control event "
                f"(rc={r.returncode}) and produced no verdict")
        return {"cube": i, "ok": verdict == "s VERIFIED", "tool": "drat-trim",
                "tool_rc": r.returncode, "verdict": verdict,
                "drat_sha256": want_sha,
                "drat_bytes": os.path.getsize(tmp_drat),
                "cnf_sha256": sha256_file(tmp_cnf),
                "check_wall_s": round(time.time() - t0, 1)}
    except subprocess.TimeoutExpired:
        return {"cube": i, "ok": False, "error": "drat-trim timeout 3600s"}
    except CheckerKilled:
        # Must come BEFORE the blanket handler, which would turn it into an
        # ok:False row and put it in CHECK_FAILURES - the exact false "the
        # mathematics failed" this guard exists to prevent.
        raise
    except Exception as e:  # noqa: BLE001 - record, never crash the pass
        return {"cube": i, "ok": False, "error": repr(e)}
    finally:
        _discard(tmp_cnf, tmp_drat)


def main():
    wavedir, workers = sys.argv[1], int(sys.argv[2])
    w = os.path.join(HERE, wavedir)
    trans = os.path.join(w, "transcripts.jsonl")
    fails = os.path.join(w, "CHECK_FAILURES.jsonl")
    # This waited on a lock named "PRUNER_RUNNING". sample_prune.py - the only
    # thing that runs the front-loaded checker - creates PRUNER_RUNNING_prune
    # and PRUNER_RUNNING_check. Nothing has created the bare name since
    # check_and_prune.py stopped being run as a script, so the guard could
    # never fire: it read as protection and was an oracle that cannot fail.
    #
    # Watch the name that exists, and bound the wait. run_campaign.py stops the
    # front-loaded checker before calling this, so a live lock here means that
    # stop did not take - worth waiting out, never worth hanging a multi-day
    # campaign on. Scratch names are per-process now, so proceeding is safe.
    lock = os.path.join(w, "PRUNER_RUNNING_check")
    waited = 0
    while os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 1800:
        if waited >= 3600:
            print("check_pass: sample checker still holds a fresh lock after "
                  "1 h; proceeding anyway (scratch names are per-process)",
                  flush=True)
            break
        print("check_pass: the front-loaded sample checker is still running; "
              "waiting", flush=True)
        time.sleep(300)
        waited += 300
    done = set()
    if os.path.exists(trans):
        with open(trans, encoding="ascii") as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["cube"])
                except Exception:  # noqa: BLE001
                    pass
    jobs = []
    skipped_pruned = 0
    verd = os.path.join(w, "verdicts")
    for _f, _p in verdict_io.iter_verdict_files(verd):
        # Phase 3 runs after days of compute. A bare json.load here would have
        # thrown JSONDecodeError on a zero-byte verdict and been reported as
        # "check pass reported failures - do NOT proceed", i.e. a corrupt file
        # read as the mathematics failing. Unreadable = nothing to check.
        v = verdict_io.read_verdict(_p)
        if v is None:
            continue
        if v.get("rc") == 20 and "drat_sha256" in v and v["cube"] not in done:
            # Proofs outside the verified sample are recorded and deleted by
            # the pruner (checking all 16,384 costs ~4,700 core-hours). An
            # absent proof here is that policy working, not a failure.
            cube = v["cube"]
            if not (os.path.exists(os.path.join(w, "drat",
                                                f"cube_{cube:05d}.drat.gz")) or
                    os.path.exists(os.path.join(w, "drat",
                                                f"cube_{cube:05d}.drat"))):
                skipped_pruned += 1
                continue
            jobs.append((wavedir, cube, v["lits"], v["drat_sha256"]))
    if skipped_pruned:
        print(f"check_pass: {skipped_pruned} cube(s) have no proof on disk "
              f"(pruned by policy); their digests stand in pruned.jsonl",
              flush=True)
    print(f"check_pass: {len(jobs)} to check, {len(done)} already done",
          flush=True)
    errlog = os.path.join(w, "CHECKER_ERRORS.jsonl")
    state = {"nfail": 0, "abort": False}

    def run_batch(batch, label):
        """Returns the jobs whose WORKER died, for one retry. A dead worker is
        an infrastructure fault; only drat-trim's own verdict is a result."""
        errored = []
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futmap = {ex.submit(check_one, j): j for j in batch}
            for n, fut in enumerate(as_completed(futmap), 1):
                try:
                    r = fut.result()
                except Exception as e:  # noqa: BLE001
                    # NOT a CHECK_FAILURES line. That file means "a proof did
                    # not verify": it halts the campaign, stops the watchdog
                    # restarting anything, and raises CAMPAIGN_ATTENTION. A
                    # crashed process recorded there would read as the
                    # mathematics failing after four days of compute.
                    errored.append(futmap[fut])
                    with open(errlog, "a", encoding="ascii",
                              newline="\n") as fh:
                        fh.write(json.dumps({"cube": futmap[fut][1],
                                             "phase": label,
                                             "error": repr(e)}) + "\n")
                    print(f"!! CHECKER ERROR cube {futmap[fut][1]} "
                          f"(infrastructure, not a proof failure): {e!r}",
                          flush=True)
                    continue
                if r["ok"]:
                    with open(trans, "a", encoding="ascii",
                              newline="\n") as fh:
                        fh.write(json.dumps(r) + "\n")
                else:
                    state["nfail"] += 1
                    with open(fails, "a", encoding="ascii",
                              newline="\n") as fh:
                        fh.write(json.dumps(r) + "\n")
                    print(f"!! CHECK FAIL cube {r['cube']}: "
                          f"{r.get('error', r.get('verdict'))}", flush=True)
                    if state["nfail"] > 10:
                        print("check_pass: >10 failures, aborting", flush=True)
                        state["abort"] = True
                        # Drop everything not yet started, or "fail fast" takes
                        # as long as the longest running proof times fourteen.
                        ex.shutdown(wait=False, cancel_futures=True)
                        break
                if n % 100 == 0 or n == len(batch):
                    el = time.time() - t0
                    print(f"check_pass[{label}]: {n}/{len(batch)} in "
                          f"{el/3600:.2f}h ({n/max(el,1)*3600:.0f}/h), "
                          f"{state['nfail']} failed", flush=True)
        return errored

    errored = run_batch(jobs, "main")
    if state["abort"]:
        return 2
    if errored:
        print(f"check_pass: retrying {len(errored)} cube(s) whose worker died",
              flush=True)
        errored = run_batch(errored, "retry")
        if state["abort"]:
            return 2

    nfail = state["nfail"]
    print(f"CHECK PASS DONE: {len(jobs) - nfail - len(errored)} verified, "
          f"{nfail} failed, {len(errored)} unchecked after a retry",
          flush=True)
    if errored:
        # Returning 0 here would let the campaign proceed as though every
        # sampled proof had been verified. It had not: these were never
        # checked. Say so with a code the orchestrator reports separately.
        print("check_pass: cubes remain UNCHECKED - this is an environment "
              "fault, not a failed proof. Do not read it as a result.",
              flush=True)
        return 3
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
