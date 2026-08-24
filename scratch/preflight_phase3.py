"""Exercise campaign phase 3 end-to-end on a throwaway wave, BEFORE it runs.

check_pass.py decides whether ~2,000 core-hours of compute is reported as a
proof or as a failure, and it does not execute until day four of the campaign.
Its three outcomes are worth separating now rather than discovering at 3 a.m.:

  A  every sampled proof verifies                       -> rc 0
  B  a proof genuinely does not verify                  -> rc 1, CHECK_FAILURES
  C  a WORKER dies (BrokenProcessPool has happened here
     before) - an environment fault, never a bad proof  -> rc 3, CHECKER_ERRORS

C is the one that matters most. Before 2026-08-20 a dead worker propagated out
of fut.result() and killed the pass, which the orchestrator read as "check pass
reported failures - do NOT proceed": a crashed process presented as a
mathematical result.

Usage: preflight_phase3.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PY = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe")
KISSAT = r"C:\dev\Tools\sat\bin\kissat.exe"
WAVE = "testwave_pf"
W = os.path.join(HERE, WAVE)
NCUBES = 6


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def php_cnf(pigeons, holes):
    def var(p, h):
        return p * holes + h + 1
    cl = [[var(p, h) for h in range(holes)] for p in range(pigeons)]
    for h in range(holes):
        for a in range(pigeons):
            for b in range(a + 1, pigeons):
                cl.append([-var(a, h), -var(b, h)])
    return pigeons * holes, cl


def build():
    if os.path.isdir(W):
        shutil.rmtree(W)
    os.makedirs(os.path.join(W, "drat"))
    os.makedirs(os.path.join(W, "verdicts"))
    nv, cl = php_cnf(11, 10)
    base = os.path.join(W, "base.cnf")
    with open(base, "w", encoding="ascii", newline="\n") as fh:
        fh.write(f"p cnf {nv} {len(cl)}\n")
        for c in cl:
            fh.write(" ".join(str(x) for x in c) + " 0\n")
    seed = os.path.join(W, "drat", "cube_00000.drat")
    r = subprocess.run([KISSAT, "-q", "--no-binary", base, seed],
                       capture_output=True, text=True)
    assert r.returncode == 20, f"fixture not UNSAT: rc={r.returncode}"
    sha = sha256_file(seed)
    size = os.path.getsize(seed)
    for i in range(1, NCUBES):
        shutil.copyfile(seed, os.path.join(W, "drat", f"cube_{i:05d}.drat"))
    for i in range(NCUBES):
        with open(os.path.join(W, "verdicts", f"v{i:05d}.json"), "w",
                  encoding="ascii", newline="\n") as fh:
            json.dump({"cube": i, "lits": [], "rc": 20, "wall_s": 1.0,
                       "drat_sha256": sha, "drat_bytes": size}, fh)
    print(f"fixture: {NCUBES} cubes, proof {size/1e6:.1f} MB each")


def reset_results():
    for f in ("transcripts.jsonl", "CHECK_FAILURES.jsonl",
              "CHECKER_ERRORS.jsonl"):
        p = os.path.join(W, f)
        if os.path.exists(p):
            os.remove(p)


def count(f):
    p = os.path.join(W, f)
    if not os.path.exists(p):
        return 0
    with open(p, encoding="ascii") as fh:
        return sum(1 for ln in fh if ln.strip())


def run_pass(workers, kill_a_worker_after=None):
    cmd = [PY, "-u", os.path.join(HERE, "check_pass.py"), WAVE, str(workers)]
    p = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    killed = None
    if kill_a_worker_after is not None:
        time.sleep(kill_a_worker_after)
        # Only this pass's own workers. The live campaign's checker is running
        # drat-trim too, and killing one of ITS workers would be a real fault
        # injected into a real run - match on parent_pid, nothing looser.
        ps = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*parent_pid={p.pid}*' }} | "
             "Select-Object -First 1 -ExpandProperty ProcessId"],
            capture_output=True, text=True)
        pid = ps.stdout.strip()
        if pid.isdigit():
            subprocess.run(["taskkill.exe", "/PID", pid, "/T", "/F"],
                           capture_output=True)
            killed = int(pid)
            print(f"  injected fault: killed worker PID {killed}")
        else:
            print("  WARNING: no worker found to kill - fault NOT injected")
    out, _ = p.communicate()
    return p.returncode, out, killed


def main():
    build()
    results = []

    print("\n=== A: every proof verifies -> expect rc 0 ===")
    reset_results()
    rc, out, _ = run_pass(3)
    print("\n".join("  " + ln for ln in out.strip().splitlines()[-4:]))
    okA = (rc == 0 and count("transcripts.jsonl") == NCUBES
           and count("CHECK_FAILURES.jsonl") == 0)
    print(f"  rc={rc} transcripts={count('transcripts.jsonl')} "
          f"failures={count('CHECK_FAILURES.jsonl')} -> "
          f"{'PASS' if okA else 'FAIL'}")
    results.append(("A all-verify rc 0", okA))

    print("\n=== B: one proof is corrupt -> expect rc 1 and a CHECK_FAILURES "
          "line ===")
    reset_results()
    victim = os.path.join(W, "drat", "cube_00003.drat")
    with open(victim, "r+b") as fh:
        fh.seek(os.path.getsize(victim) // 2)
        fh.write(b"\n0 0 0 0\n")
    rc, out, _ = run_pass(3)
    print("\n".join("  " + ln for ln in out.strip().splitlines()[-4:]))
    okB = (rc == 1 and count("CHECK_FAILURES.jsonl") == 1
           and count("transcripts.jsonl") == NCUBES - 1)
    print(f"  rc={rc} transcripts={count('transcripts.jsonl')} "
          f"failures={count('CHECK_FAILURES.jsonl')} -> "
          f"{'PASS' if okB else 'FAIL'}")
    results.append(("B bad-proof rc 1", okB))

    print("\n=== C: a worker is killed mid-pass -> expect the cubes to be "
          "RETRIED, and NOT recorded as failed proofs ===")
    build()          # restore the corrupted proof
    reset_results()
    rc, out, killed = run_pass(3, kill_a_worker_after=4)
    tail = out.strip().splitlines()
    print("\n".join("  " + ln for ln in tail[-6:]))
    retried = any("retrying" in ln for ln in tail)
    okC = (killed is not None and count("CHECKER_ERRORS.jsonl") > 0
           and retried and count("CHECK_FAILURES.jsonl") == 0
           and rc in (0, 3))
    print(f"  rc={rc} errors={count('CHECKER_ERRORS.jsonl')} "
          f"retried={retried} transcripts={count('transcripts.jsonl')} "
          f"proof-failures={count('CHECK_FAILURES.jsonl')} -> "
          f"{'PASS' if okC else 'FAIL'}")
    print("  (a dead worker must never reach CHECK_FAILURES.jsonl - that file "
          "means the mathematics failed)")
    results.append(("C worker-death is not a proof failure", okC))

    print("\n=== SUMMARY ===")
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(W, ignore_errors=True)
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
