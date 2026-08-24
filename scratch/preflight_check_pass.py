"""Prove campaign phase 3 works BEFORE it runs, not after three days of compute.

Phase 3 calls check_pass.check_one on every retained proof. If it fails, the
orchestrator exits with "HALT: check pass reported failures - do NOT proceed"
and the campaign is over. It had a real defect on 19 Aug (it read only .drat.gz
while proofs now land raw); this runs the patched function against actual
retained proofs so the fix is observed, not asserted.

Runs on the smallest sampled proof first (fast plumbing check) and then a large
one (real scale, real timing). Usage: preflight_check_pass.py [n_small] [n_big]
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_pass import check_one  # noqa: E402

W = os.path.join(HERE, "wave274tot")


def candidates():
    sample = set(json.load(open(os.path.join(W, "sample.json"),
                                encoding="ascii"))["cubes"])
    out = []
    for f in os.listdir(os.path.join(W, "drat")):
        if not f.endswith(".drat"):
            continue
        cube = int(f[len("cube_"):-len(".drat")])
        vp = os.path.join(W, "verdicts", f"v{cube:05d}.json")
        if not os.path.exists(vp):
            continue
        v = json.load(open(vp, encoding="ascii"))
        if v.get("rc") != 20 or not v.get("drat_sha256"):
            continue
        if cube not in sample:
            continue
        size = os.path.getsize(os.path.join(W, "drat", f))
        # A proof still being written has no verdict yet, so anything here is
        # complete - but re-read the size against the verdict to be sure.
        if size != v.get("drat_bytes"):
            continue
        out.append((size, cube, v))
    out.sort()
    return out


def run(cube, v, label):
    mb = v["drat_bytes"] / 1e6
    print(f"--- {label}: cube {cube}, {mb:.1f} MB ---", flush=True)
    t0 = time.time()
    r = check_one(("wave274tot", cube, v["lits"], v["drat_sha256"]))
    el = time.time() - t0
    ok = r.get("ok")
    print(f"    ok={ok} verdict={r.get('verdict')!r} "
          f"err={r.get('error')!r} tool_rc={r.get('tool_rc')} "
          f"wall={el:.0f}s", flush=True)
    return ok


def main():
    n_small = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    n_big = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    c = candidates()
    print(f"{len(c)} retained sampled proofs eligible; "
          f"sizes {c[0][0]/1e6:.1f} MB .. {c[-1][0]/1e6:.1f} MB", flush=True)
    results = []
    for i in range(min(n_small, len(c))):
        _, cube, v = c[i]
        results.append(run(cube, v, f"small {i+1}"))
    for i in range(min(n_big, len(c))):
        _, cube, v = c[-(i + 1)]
        results.append(run(cube, v, f"large {i+1}"))
    good = sum(1 for r in results if r)
    print()
    print(f"PREFLIGHT: {good}/{len(results)} proofs drat-trim VERIFIED")
    return 0 if good == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
