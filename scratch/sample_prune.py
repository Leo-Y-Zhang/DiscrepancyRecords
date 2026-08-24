"""Bound the wave's disk, and verify a DECLARED random sample of its proofs.

Measured 2026-08-19 on real cubes: drat-trim costs 6.1x what kissat spent
solving the cube (mean 136 s per proof against 169 s per solve, with a heavy
tail - one proof took 580 s). Checking all 16,384 proofs is therefore ~4,700
core-hours, about twelve days on this machine: not available. Hoarding the
proofs instead needs ~260 GB against 390 GB free, with no margin.

So this does two separate jobs and keeps them distinguishable:

  SAMPLED cubes (a uniform random sample, seed and ids written to
  sample.json before any checking) get their proof decompressed, hashed
  against the digest the solver recorded, and verified by drat-trim. Result
  goes to transcripts.jsonl.

  EVERY OTHER cube has its proof digest and size recorded in pruned.jsonl
  and the proof bytes deleted at once.

Honesty note carried into the claim: a 10% proof sample is WEAK against a
single bad cube (a lone error survives sampling with probability ~0.9). The
per-cube guarantee comes from solving every cube twice - a second encoding
under a second solver - not from this sample. This sample tests the proof
pipeline, not each cube.

A proof that fails to verify is KEPT and halts this loop.

Usage: sample_prune.py <wavedir> <total_cubes> <sample_size> <workers>
"""
import json
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verdict_io  # noqa: E402
from check_and_prune import check_one, load_done  # noqa: E402
from console_immunity import ignore_console_signals  # noqa: E402

# Same reason as run_campaign.py: a console Ctrl+C killed this campaign four
# times. The pruner and the checker run for days beside the wave and must be
# just as unreachable from a terminal. See detach.py.
ignore_console_signals()

# NOT 20260819. cube_wave2.py fixes the processing order with that seed, and
# CPython's random.sample takes its pool branch here (n=16384 <= setsize
# 16405), walking the identical _randbelow stream as random.shuffle. Drawn with
# 20260819 the "sample" came out exactly equal to the last 1600 entries of the
# processing order - verified by set equality, and the reason sample.json was
# re-declared by redeclare_sample.py on 2026-08-19. A sample must never share a
# seed with the order of the run it is sampling.
SEED = 4177213
ARCHIVE_EVERY = 512


def declare_sample(w, total, size):
    """Write the sample before checking anything, so it cannot be cherry-picked."""
    path = os.path.join(w, "sample.json")
    if os.path.exists(path):
        with open(path, encoding="ascii") as fh:
            return set(json.load(fh)["cubes"])
    cubes = sorted(random.Random(SEED).sample(range(total), size))
    doc = {"schema": "proof-sample.v1", "seed": SEED, "method":
           "random.Random(seed).sample(range(n_cubes), size), sorted",
           "n_cubes": total, "size": size,
           "archive_rule": f"proofs for cubes with id % {ARCHIVE_EVERY} == 0 "
                           f"are retained on disk after verification",
           "cubes": cubes}
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        json.dump(doc, fh)
    return set(cubes)


def main():
    wavedir, total, size, workers = (sys.argv[1], int(sys.argv[2]),
                                     int(sys.argv[3]), int(sys.argv[4]))
    # Pruning is fast and disk-critical; checking a sampled proof takes
    # minutes. Run them as separate processes or the reclaim loop starves
    # behind a verification batch and the disk fills anyway.
    mode = sys.argv[5] if len(sys.argv) > 5 else "both"
    w = os.path.join(HERE, wavedir)
    verd, drat = os.path.join(w, "verdicts"), os.path.join(w, "drat")
    trans = os.path.join(w, "transcripts.jsonl")
    pruned_log = os.path.join(w, "pruned.jsonl")
    fails = os.path.join(w, "CHECK_FAILURES.jsonl")
    # One stop file per MODE. Both modes shared "STOP_CHECKER", so asking the
    # checker to stand down for campaign phase 3 also stopped the pruner - and
    # worse, a watchdog restart of the pruner deleted the stop file at startup
    # and quietly revived the checker it was meant to retire.
    stop = os.path.join(w, {"check": "STOP_CHECKER",
                            "prune": "STOP_PRUNER"}.get(mode, "STOP_CHECKER"))
    lock = os.path.join(w, f"PRUNER_RUNNING_{mode}")
    if os.path.exists(stop):
        os.remove(stop)
    sample = declare_sample(w, total, size)
    open(lock, "w", encoding="ascii").write(str(os.getpid()))
    checked = load_done(trans)
    pruned = load_done(pruned_log)
    print(f"sample-pruner: sample of {len(sample)} of {total}; "
          f"{len(checked)} checked, {len(pruned)} pruned so far", flush=True)
    nfail = 0
    try:
        while len(checked | pruned) < total and not os.path.exists(stop):
            os.utime(lock, None)
            todo = []
            freed = 0
            for f in sorted(os.listdir(drat)):
                # The gzip loop is retired: compressing a proof we are about
                # to delete was pure waste, so proofs now arrive raw. Older
                # ones on disk are still .gz, so handle both.
                if f.endswith(".drat.gz"):
                    cube = int(f[len("cube_"):-len(".drat.gz")])
                elif f.endswith(".drat"):
                    cube = int(f[len("cube_"):-len(".drat")])
                else:
                    continue
                if cube in checked or cube in pruned:
                    continue
                # read_verdict, not json.load: a zero-byte verdict killed the
                # pruner here on 2026-08-22. Unreadable means "not solved" -
                # leave the proof alone and let the driver re-solve the cube.
                v = verdict_io.read_verdict(os.path.join(verd,
                                                         f"v{cube:05d}.json"))
                if v is None or v.get("rc") != 20 or not v.get("drat_sha256"):
                    continue
                # gzip_loop.py used to delete each solved cube's .cnf and was
                # retired; nothing replaced it, so they leaked at ~16 MB each
                # (260 GB over a full wave). The verdict is written, so the cnf
                # is spent: every checker regenerates it from base.cnf + lits.
                cnf = os.path.join(w, f"cube_{cube:05d}.cnf")
                if os.path.exists(cnf):
                    try:
                        freed += os.path.getsize(cnf)
                        os.remove(cnf)
                    except OSError:
                        pass
                if cube in sample:
                    if mode != "prune":
                        todo.append((wavedir, cube, v["lits"],
                                     v["drat_sha256"]))
                elif mode != "check":
                    gz = os.path.join(drat, f)
                    # The declaration promises every 512th proof survives as a
                    # spot-check artifact a reader can fetch without
                    # regenerating anything. Keep that promise here, or the
                    # archive_rule line in sample.json is simply false.
                    archived = cube % ARCHIVE_EVERY == 0
                    try:
                        size = os.path.getsize(gz)
                        if not archived:
                            os.remove(gz)
                    except OSError:
                        continue  # still being written; next pass gets it
                    if not archived:
                        freed += size
                    with open(pruned_log, "a", encoding="ascii",
                              newline="\n") as fh:
                        fh.write(json.dumps(
                            {"cube": cube, "drat_sha256": v["drat_sha256"],
                             "drat_bytes": v["drat_bytes"],
                             "proof_pruned": not archived,
                             "archived_on_disk": archived,
                             "checked": False}) + "\n")
                    pruned.add(cube)
            if freed:
                print(f"sample-pruner: freed {freed/1e9:.2f} GB, "
                      f"{len(pruned)} pruned, {len(checked)} checked",
                      flush=True)
            if not todo:
                time.sleep(120)
                continue
            with ProcessPoolExecutor(max_workers=workers) as ex:
                futmap = {ex.submit(check_one, j): j for j in todo[:60]}
                for fut in as_completed(futmap):
                    try:
                        row = fut.result()
                    except Exception as e:  # noqa: BLE001
                        # An exception here used to propagate and kill the
                        # checker outright, leaving the watchdog to notice
                        # five minutes later. It is an environment fault, not
                        # a proof result: log it, leave the cube unchecked so
                        # the next pass picks it up, and keep going. It must
                        # never reach CHECK_FAILURES.jsonl, which halts the
                        # campaign and means "a proof did not verify".
                        with open(os.path.join(w, "CHECKER_ERRORS.jsonl"), "a",
                                  encoding="ascii", newline="\n") as fh:
                            fh.write(json.dumps({"cube": futmap[fut][1],
                                                 "error": repr(e)}) + "\n")
                        print(f"!! CHECKER ERROR cube {futmap[fut][1]} "
                              f"(infrastructure, not a proof failure): {e!r}",
                              flush=True)
                        continue
                    row["sampled"] = True
                    checked.add(row["cube"])
                    if row["ok"]:
                        with open(trans, "a", encoding="ascii",
                                  newline="\n") as fh:
                            fh.write(json.dumps(row) + "\n")
                    else:
                        nfail += 1
                        with open(fails, "a", encoding="ascii",
                                  newline="\n") as fh:
                            fh.write(json.dumps(row) + "\n")
                        print(f"!! CHECK FAILURE cube {row['cube']}: "
                              f"{row.get('error') or row.get('verdict')}",
                              flush=True)
                    if os.path.exists(stop):
                        # The while-loop's stop check is at the TOP of an
                        # iteration, and one iteration is up to 60 proofs at a
                        # single worker - twenty hours. Campaign phase 3 asks
                        # this process to stand down before it starts, so the
                        # request has to be honoured inside the batch or the
                        # handover never happens.
                        print("sample-pruner: STOP_CHECKER seen, standing down "
                              "mid-batch", flush=True)
                        ex.shutdown(wait=False, cancel_futures=True)
                        break
            if nfail:
                print(f"sample-pruner: HALTING, {nfail} failure(s)", flush=True)
                return 1
            print(f"sample-pruner: {len(checked)}/{len(sample)} sample "
                  f"verified, {len(pruned)} pruned", flush=True)
        print(f"sample-pruner done: {len(checked)} verified of "
              f"{len(sample)} sampled, {len(pruned)} pruned, {nfail} failures",
              flush=True)
        return 0 if nfail == 0 else 1
    finally:
        if os.path.exists(lock):
            os.remove(lock)
        # check_pass.py (the campaign's later phase) waits on this name
        legacy = os.path.join(w, "PRUNER_RUNNING")
        if mode == "check" and os.path.exists(legacy):
            os.remove(legacy)


if __name__ == "__main__":
    raise SystemExit(main())
