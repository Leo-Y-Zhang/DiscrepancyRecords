"""Cube-and-conquer driver, generation 2: parameterized encoder, split depth
and directories. Same verdict/proof discipline as cube_wave.py.

Usage:
  cube_wave2.py gen <encoder> <nsplit> <wavedir>
  cube_wave2.py sample <wavedir> <n> <budget_s> <workers>   (random ids, seeded)
  cube_wave2.py wave <wavedir> <workers> <budget_s> [--no-proof]
  cube_wave2.py status <wavedir>
"""
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import verdict_io

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "nk2snap"))
KISSAT = r"C:\dev\Tools\sat\bin\kissat.exe"
N, K, L = 274, 17, 2
SNAPSHOT_COMMIT = "54ca57814f25daf06a644efdea9ac4ec6a431c5c"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def wave_paths(wavedir):
    w = os.path.join(HERE, wavedir)
    return w, os.path.join(w, "base.cnf"), os.path.join(w, "cubes.txt"), \
        os.path.join(w, "verdicts"), os.path.join(w, "drat")


def gen(encoder, nsplit, wavedir):
    from nk2.dimacs import write_cnf
    if encoder == "totalizer":
        from nk2.encode_totalizer import build
    elif encoder == "seqcount":
        from nk2.encode_seqcount import build
    else:
        raise SystemExit(f"unknown encoder {encoder}")
    w, base, cubes, verd, drat = wave_paths(wavedir)
    for d in (w, verd, drat):
        os.makedirs(d, exist_ok=True)
    n_vars, cl = build(N, K, L, symmetry_break=True)
    info = write_cnf(base, n_vars, cl)
    occ = [0] * (N + 1)
    with open(base, encoding="ascii") as fh:
        next(fh)
        for line in fh:
            for tok in line.split()[:-1]:
                v = abs(int(tok))
                if 1 <= v <= N:
                    occ[v] += 1
    split = sorted(range(1, N + 1), key=lambda v: (-occ[v], v))[:nsplit]
    split.sort()
    with open(cubes, "w", encoding="ascii", newline="\n") as fh:
        for mask in range(1 << nsplit):
            lits = [split[i] if (mask >> i) & 1 else -split[i]
                    for i in range(nsplit)]
            fh.write(" ".join(str(x) for x in lits) + "\n")
    meta = {"schema": "cube-wave.v2", "N": N, "k": K, "l": L,
            "encoder": encoder, "symmetry_break": True,
            "snapshot_commit": SNAPSHOT_COMMIT,
            "base": {k2: info[k2] for k2 in ("n_vars", "n_clauses", "sha256")},
            "split_vars": split, "n_cubes": 1 << nsplit,
            "cubes_sha256": sha256_file(cubes),
            "cube_construction": "all 2^%d sign patterns of split_vars, "
                                 "mask bit i -> sign of split_vars[i]" % nsplit}
    with open(os.path.join(w, "manifest.json"), "w", encoding="ascii",
              newline="\n") as fh:
        json.dump(meta, fh, indent=1)
    print(json.dumps({k2: meta[k2] for k2 in
                      ("encoder", "base", "split_vars", "n_cubes")}, indent=1))


def solve_cube(args):
    wavedir, i, lits, budget, want_proof = args
    w, base, cubes, verd, drat = wave_paths(wavedir)
    vpath = os.path.join(verd, f"v{i:05d}.json")
    # An unreadable cached verdict means "not solved", so we fall through and
    # solve it again, overwriting the debris. Before 2026-08-22 this was a bare
    # json.load and a zero-byte file killed the worker instead.
    cached = verdict_io.read_verdict(vpath)
    if cached is not None:
        if cached["rc"] in (10, 20) and (not want_proof
                                         or "drat_sha256" in cached):
            return cached
    with open(base, encoding="ascii") as fh:
        header = fh.readline().split()
        body = fh.read()
    p = os.path.join(w, f"cube_{i:05d}.cnf")
    with open(p, "w", encoding="ascii", newline="\n") as fh:
        fh.write(f"p cnf {header[2]} {int(header[3]) + len(lits)}\n")
        fh.write(body)
        for x in lits:
            fh.write(f"{x} 0\n")
    proof = os.path.join(drat, f"cube_{i:05d}.drat") if want_proof else None
    cmd = [KISSAT, "-q", "--no-binary", p, proof] if proof else \
        [KISSAT, "-q", p]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=budget)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        rc = None
    wall = time.time() - t0
    v = {"cube": i, "lits": [int(x) for x in lits], "rc": rc,
         "wall_s": round(wall, 2)}
    if rc == 20 and proof:
        v["drat_sha256"] = sha256_file(proof)
        v["drat_bytes"] = os.path.getsize(proof)
    if rc == 10:
        v["stdout_tail"] = r.stdout[-40000:]
    # ROOT CAUSE OF THE 2026-08-22 OUTAGE, fixed here. `open(vpath, "w")`
    # creates the file before json.dump fills it, so a kill in between leaves a
    # ZERO-BYTE verdict - and every reader in the campaign then died on it.
    # write_verdict goes via a temp file + os.replace: a kill now leaves either
    # no file or a complete one.
    verdict_io.write_verdict(vpath, v)
    if rc != 20 or not proof:
        for junk in (p, proof):
            if junk and os.path.exists(junk):
                os.remove(junk)
    return v


def run_many(wavedir, ids, budget, want_proof, workers):
    _, _, cubes, _, _ = wave_paths(wavedir)
    with open(cubes, encoding="ascii") as fh:
        all_cubes = []
        for line in fh:
            toks = line.split()
            if toks and toks[0] == "a":
                toks = toks[1:]
            if toks and toks[-1] == "0":
                toks = toks[:-1]
            if toks:
                all_cubes.append(toks)
    jobs = [(wavedir, i, all_cubes[i], budget, want_proof) for i in ids]
    t0 = time.time()
    done = unsat = sat = unk = 0
    times = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(solve_cube, j) for j in jobs]
        for f in as_completed(futs):
            v = f.result()
            done += 1
            times.append(v["wall_s"])
            if v["rc"] == 20:
                unsat += 1
            elif v["rc"] == 10:
                sat += 1
                print(f"!! CUBE {v['cube']} SAT - witness candidate", flush=True)
            else:
                unk += 1
            if done % 50 == 0 or done == len(jobs):
                el = time.time() - t0
                rate = done / el * 3600
                left = (len(jobs) - done) / max(rate, 1e-9)
                print(f"{done}/{len(jobs)} in {el/3600:.2f}h | UNSAT {unsat} "
                      f"SAT {sat} UNK {unk} | {rate:.0f}/h | ~{left:.1f}h left",
                      flush=True)
    return unsat, sat, unk, times


def main():
    mode = sys.argv[1]
    if mode == "gen":
        gen(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    elif mode == "sample":
        wavedir, n, budget, workers = sys.argv[2], int(sys.argv[3]), \
            int(sys.argv[4]), int(sys.argv[5])
        _, _, cubes, _, _ = wave_paths(wavedir)
        total = sum(1 for _ in open(cubes, encoding="ascii"))
        ids = random.Random(20260819).sample(range(total), n)
        unsat, sat, unk, times = run_many(wavedir, ids, budget, False, workers)
        times.sort()
        print(f"SAMPLE: UNSAT {unsat} SAT {sat} UNK {unk}; "
              f"med {times[len(times)//2]:.1f}s mean {sum(times)/len(times):.1f}s "
              f"p90 {times[int(0.9*len(times))]:.1f}s", flush=True)
        est = sum(times) / len(times) * total
        print(f"EXTRAPOLATION: ~{est/3600:.0f} core-hours", flush=True)
    elif mode == "wave":
        wavedir, workers, budget = sys.argv[2], int(sys.argv[3]), \
            int(sys.argv[4])
        want_proof = "--no-proof" not in sys.argv
        _, _, cubes, verd, _ = wave_paths(wavedir)
        total = sum(1 for _ in open(cubes, encoding="ascii"))
        ids = list(range(total))
        random.Random(20260819).shuffle(ids)
        unsat, sat, unk, _ = run_many(wavedir, ids, budget, want_proof, workers)
        print(f"WAVE DONE: UNSAT {unsat} SAT {sat} UNK {unk}", flush=True)
    elif mode == "status":
        wavedir = sys.argv[2]
        _, _, _, verd, _ = wave_paths(wavedir)
        print(json.dumps(verdict_io.count_verdicts(verd)))


if __name__ == "__main__":
    main()
