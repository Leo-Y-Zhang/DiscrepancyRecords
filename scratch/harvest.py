"""Turn the finished campaign into a committed, gate-verified claim.

Run when DONE.json exists. Everything up to the push is automatic; the push and
the OEIS submission are NOT, because they are outward-facing.

    python scratch/harvest.py --self-test    # validate the claim SHAPE now
    python scratch/harvest.py --preflight    # check the waves, change nothing
    python scratch/harvest.py --apply        # import, write the claim, gate

Why this exists three days before it is needed: the campaign is expected to
finish around 27 Aug and the operator's subscription may lapse shortly after, so
the harvest must not depend on a session being alive to improvise it. It also
means the claim shape is validated against gate/verify_all.py's OWN constants
now, rather than discovered to be wrong at the end.

THE CLAIM THIS BUILDS
    N17_2_exact_274, kind "exact", value 274.
    Lower side: evidence/witnesses/k17_l2_N273.txt - ALREADY on record and
      already the basis of the committed N17_2_lower_274 claim. No compute.
    Upper side: two complete waves, both UNSAT, from DIFFERENT encoders -
      wave274tot (totalizer, 16384 cubes) confirmed by wave274 (seqcount,
      4096 cubes). That pairing is what W6 requires and what lifts the claim to
      `unsat-dual`.
    transcripts are null on both sides: the waves ran --no-proof. TDD.md is
      explicit that this imports and verifies at unsat-wave, and that
      wave-drat-verified sorts BELOW unsat-dual anyway.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "gate"))

WAVES = {
    "wave274tot": {"encoder": "totalizer", "n_cubes": 16384,
                   "base_sha": "356f1adef74defd5eefdd9de05a6234bc34a86145a109d1fc3d51bd3f1e1addc",
                   "cubes_sha": "fbfa8650d71bb4b85a1f2d2e595a32c77d76da70ea47b2a1808567e9ef40ece8"},
    "wave274": {"encoder": "seqcount", "n_cubes": 4096,
                "base_sha": "9ad0c62e5225e27d40ac98d6ef7154718abf354d22c28a76167db7dfefc24e52",
                "cubes_sha": "d17be007a489a35d1199c25a483dc941ab2dcf5407edded23a430508f9d7ec87"},
}
WITNESS = "evidence/witnesses/k17_l2_N273.txt"
WITNESS_SHA = ("b12629196a74c6819602f89479023935"
               "e004942a298ae3ae94483015062f9a5a")


def build_claim():
    """The claim, in the exact shape gate/verify_all.py demands."""
    return {
        "id": "N17_2_exact_274",
        "k": 17,
        "l": 2,
        "kind": "exact",
        "value": 274,
        "witness": {"path": WITNESS, "sha256": WITNESS_SHA},
        "unsat_runs": [],
        "drat": None,
        "wave": {
            "manifest": "evidence/waves/wave274tot/manifest.json",
            "verdicts_dir": "evidence/waves/wave274tot/verdicts",
            "transcripts": None,
            "confirm": {
                "kind": "wave",
                "manifest": "evidence/waves/wave274/manifest.json",
                "verdicts_dir": "evidence/waves/wave274/verdicts",
                "transcripts": None,
            },
        },
        "evidence_level": "unsat-dual",
        "prior_art": (
            "a(17) of OEIS A398541 is open; the published lower bound is "
            "N(17,2) >= 273 (T. A. Lystad, Zenodo DOI 10.5281/zenodo.21840279, "
            "periodic witness at N = 272). The lower side here, a witness at "
            "N = 273, is claimed by this repository as N17_2_lower_274. No "
            "upper bound for k = 17 has been published to our knowledge."
        ),
        "notes": (
            "REPLACE: solver identity, machine, dates. Upper side: every one of "
            "the 16384 totalizer cubes at N = 274 returned UNSAT, and the 4096 "
            "seqcount cubes of the confirming wave did likewise - two different "
            "encodings of the same question, which is what W6 requires and what "
            "carries this to unsat-dual. Both waves ran the driver's --no-proof "
            "mode, so no verdict carries a proof digest and transcripts are "
            "null on both sides; per docs/TDD.md that is the honest shape for a "
            "wave whose worth rests on encoder diversity rather than proof "
            "volume, and wave-drat-verified sorts below unsat-dual regardless. "
            "The cube sets are reproducible: base.cnf and cubes.txt of each wave "
            "hash to the values recorded in its manifest, regenerable from "
            "snapshot_commit 54ca57814f25daf06a644efdea9ac4ec6a431c5c."
        ),
    }


def self_test():
    """Validate the claim against the gate's OWN key sets, not my reading."""
    import verify_all as G
    c = build_claim()
    ok = True

    def chk(name, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f"  {'OK  ' if good else 'FAIL'} {name}")
        if not good:
            print(f"       missing {sorted(want - got)}  extra {sorted(got - want)}")

    print("claim shape vs gate/verify_all.py constants:")
    chk("claim keys == CLAIM_KEYS", set(c), G.CLAIM_KEYS)
    chk("witness keys == WITNESS_KEYS", set(c["witness"]), G.WITNESS_KEYS)
    chk("wave keys == WAVE_KEYS", set(c["wave"]), G.WAVE_KEYS)
    chk("confirm keys == CONFIRM_WAVE_KEYS",
        set(c["wave"]["confirm"]), G.CONFIRM_WAVE_KEYS)
    lvl = c["evidence_level"]
    good = lvl in getattr(G, "EVIDENCE_ORDER", {lvl: 0})
    print(f"  {'OK  ' if good else 'FAIL'} evidence_level {lvl!r} is a known tier")
    ok = ok and good
    wp = os.path.join(REPO, WITNESS)
    import hashlib
    if os.path.exists(wp):
        h = hashlib.sha256(open(wp, "rb").read()).hexdigest()
        good = h == WITNESS_SHA
        ok = ok and good
        print(f"  {'OK  ' if good else 'FAIL'} witness on disk hashes to the recorded value")
    else:
        ok = False
        print(f"  FAIL witness missing at {wp}")
    print("\nSHAPE OK" if ok else "\nSHAPE WRONG - fix before harvest")
    return 0 if ok else 1


def preflight():
    """Are the waves actually finished and clean? Changes nothing."""
    ok = True
    for name, spec in WAVES.items():
        w = os.path.join(HERE, name)
        print(f"--- {name} ({spec['encoder']}) ---")
        if not os.path.isdir(w):
            print("  FAIL missing")
            ok = False
            continue
        mf = os.path.join(w, "manifest.json")
        m = json.load(open(mf, encoding="ascii")) if os.path.exists(mf) else {}
        for key, want in (("base sha", spec["base_sha"]),
                          ("cubes sha", spec["cubes_sha"])):
            got = m.get("base", {}).get("sha256") if key == "base sha" else m.get("cubes_sha256")
            good = got == want
            ok = ok and good
            print(f"  {'OK  ' if good else 'FAIL'} {key} matches the pre-loss manifest")
        rc = {}
        vd = os.path.join(w, "verdicts")
        n = 0
        for f in os.listdir(vd) if os.path.isdir(vd) else []:
            if f.startswith("v") and f.endswith(".json"):
                try:
                    d = json.load(open(os.path.join(vd, f), encoding="ascii"))
                except Exception:
                    rc["unreadable"] = rc.get("unreadable", 0) + 1
                    continue
                n += 1
                rc[d.get("rc")] = rc.get(d.get("rc"), 0) + 1
        complete = n == spec["n_cubes"]
        nosat = rc.get(10, 0) == 0
        allunsat = rc.get(20, 0) == spec["n_cubes"]
        ok = ok and complete and nosat and allunsat
        print(f"  {'OK  ' if complete else 'FAIL'} {n}/{spec['n_cubes']} verdicts")
        print(f"  {'OK  ' if nosat else 'FAIL'} SAT verdicts: {rc.get(10, 0)}"
              "  (any SAT DISPROVES 274)")
        print(f"  {'OK  ' if allunsat else 'FAIL'} rc histogram {rc}")
    print("\nPREFLIGHT PASS" if ok else "\nPREFLIGHT FAIL - do not harvest")
    return 0 if ok else 1


def apply():
    if preflight() != 0:
        return 1
    for name in WAVES:
        print(f"\n=== importing {name} ===")
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "tools", "import_wave.py"),
             "--source", os.path.join(HERE, name), "--name", name],
            cwd=REPO)
        if r.returncode != 0:
            print(f"import of {name} FAILED rc={r.returncode}")
            return 1
    p = os.path.join(REPO, "claims", "CLAIMS.json")
    doc = json.load(open(p, encoding="utf-8"))
    if any(c["id"] == "N17_2_exact_274" for c in doc["claims"]):
        print("claim already present; not duplicating")
    else:
        doc["claims"].append(build_claim())
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
        print("claim N17_2_exact_274 written")
    print("\n=== GATE ===")
    g = subprocess.run([sys.executable, os.path.join(REPO, "gate", "verify_all.py")],
                       cwd=REPO)
    print(f"gate rc={g.returncode}")
    print("\nNEXT, AND NOT AUTOMATIC: fill the REPLACE: fields in notes, run the")
    print("full suite, then the operator's explicit go for push and for OEIS.")
    return g.returncode


ap = argparse.ArgumentParser()
ap.add_argument("--self-test", action="store_true")
ap.add_argument("--preflight", action="store_true")
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()
if a.self_test:
    raise SystemExit(self_test())
if a.preflight:
    raise SystemExit(preflight())
if a.apply:
    raise SystemExit(apply())
ap.print_help()
