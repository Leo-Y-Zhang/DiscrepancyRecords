"""Two checkers must be able to verify the SAME cube at once without one
destroying the other's work.

Why this exists. Campaign phase 3 runs `check_pass.py wave274tot <workers>`
while the front-loaded `sample_prune.py ... check` is STILL RUNNING - the
watchdog restarts it every five minutes. check_pass has a guard meant to stop
that overlap, but it waits on a lock file named `PRUNER_RUNNING`, and
sample_prune creates `PRUNER_RUNNING_prune` / `PRUNER_RUNNING_check`. Nothing
ever creates the bare name, so the guard can never fire: it is an oracle that
cannot fail.

With the guard dead, both checkers select from the same sampled cube ids and
both build their scratch files at the SAME two paths, `chk_<cube>.cnf` and
`chk_<cube>.drat`. Whichever finishes first deletes the other's input from
under a running drat-trim. The loser reports `proof sha ... != recorded ...`
or a raw OSError, which every caller writes to CHECK_FAILURES.jsonl - and a
CHECK_FAILURES line halts the campaign (`run_campaign.py` exit 5), makes the
watchdog refuse to restart, and raises CAMPAIGN_ATTENTION.

That is the worst shape of failure available here: after ~4 days of compute it
reads as "a proof did not verify", i.e. as the mathematics failing, when it is
two processes sharing a filename.

Usage: test_checker_isolation.py [n_concurrent]
Exit 0 = isolated. Exit 1 = collision reproduced.
"""
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
KISSAT = r"C:\dev\Tools\sat\bin\kissat.exe"
WAVE = "testwave_iso"
# A multiple of KEEP_EVERY (512), so check_and_prune's archive rule KEEPS the
# proof after verifying. Any other id and the first worker home deletes the
# shared proof, which is a second, separate race - closed by giving phase 3 a
# single checker, not by naming. This test must fail for one reason only.
CUBE = 512


def php_cnf(pigeons, holes):
    """Pigeonhole: unsatisfiable, and deliberately resolution-hard so the
    proof is large enough for the race window to be real rather than lucky."""
    def var(p, h):
        return p * holes + h + 1
    clauses = []
    for p in range(pigeons):
        clauses.append([var(p, h) for h in range(holes)])
    for h in range(holes):
        for p1 in range(pigeons):
            for p2 in range(p1 + 1, pigeons):
                clauses.append([-var(p1, h), -var(p2, h)])
    return pigeons * holes, clauses


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_fixture():
    w = os.path.join(HERE, WAVE)
    if os.path.isdir(w):
        shutil.rmtree(w)
    os.makedirs(os.path.join(w, "drat"))
    os.makedirs(os.path.join(w, "verdicts"))
    nv, cl = php_cnf(11, 10)
    base = os.path.join(w, "base.cnf")
    with open(base, "w", encoding="ascii", newline="\n") as fh:
        fh.write(f"p cnf {nv} {len(cl)}\n")
        for c in cl:
            fh.write(" ".join(str(x) for x in c) + " 0\n")
    proof = os.path.join(w, "drat", f"cube_{CUBE:05d}.drat")
    t0 = time.time()
    r = subprocess.run([KISSAT, "-q", "--no-binary", base, proof],
                       capture_output=True, text=True)
    solve_s = time.time() - t0
    if r.returncode != 20:
        raise SystemExit(f"fixture is not UNSAT (kissat rc={r.returncode})")
    sha = sha256_file(proof)
    print(f"fixture: {nv} vars, {len(cl)} clauses, proof "
          f"{os.path.getsize(proof)/1e6:.2f} MB, solved in {solve_s:.1f}s")
    return sha


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    sha = build_fixture()
    from check_and_prune import check_one

    args = (WAVE, CUBE, [], sha)
    print(f"running {n} concurrent check_one() calls on the SAME cube {CUBE}")
    rows, hard_errors = [], []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(check_one, args) for _ in range(n)]
        for fut in as_completed(futs):
            try:
                rows.append(fut.result())
            except Exception as e:  # noqa: BLE001 - a crashed worker is a failure too
                hard_errors.append(repr(e))
    el = time.time() - t0

    bad = [r for r in rows if not r.get("ok")]
    print(f"  {len(rows)} returned, {len(hard_errors)} workers crashed, "
          f"{el:.1f}s wall")
    for r in bad:
        print(f"  !! NOT OK: {json.dumps(r)[:220]}")
    for e in hard_errors:
        print(f"  !! WORKER CRASHED: {e}")

    strays = [f for f in os.listdir(os.path.join(HERE, WAVE))
              if f.startswith("chk_")]
    if strays:
        print(f"  !! {len(strays)} stray scratch file(s) left behind: {strays[:6]}")

    ok = not bad and not hard_errors and not strays
    print("RESULT:", "PASS - checkers are isolated" if ok else
          "FAIL - concurrent checkers collide")
    shutil.rmtree(os.path.join(HERE, WAVE), ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
