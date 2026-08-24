"""Regenerate the phase-4 seqcount wave and PROVE it matches the lost original.

Same two-step as the totalizer wave: cube_wave2.gen builds base.cnf and a
bare-literal cubes.txt, but the wave that produced the 109 recorded verdicts
serialised cubes in iCNF form ("a <lits> 0"), which is what the recorded
cubes_sha256 was taken over. Rewrite in that form and check both hashes
against the values from the deleted manifest.

Recorded, from wave274/manifest.json before the loss:
  base.sha256   9ad0c62e5225e27d40ac98d6ef7154718abf354d22c28a76167db7dfefc24e52
  cubes_sha256  d17be007a489a35d1199c25a483dc941ab2dcf5407edded23a430508f9d7ec87
  n_vars 636754  n_clauses 1303901  n_cubes 4096
  split_vars [129,130,131,133,134,136,137,138,139,141,145,146]
"""
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
W = os.path.join(HERE, "wave274")

WANT_BASE = "9ad0c62e5225e27d40ac98d6ef7154718abf354d22c28a76167db7dfefc24e52"
WANT_CUBES = "d17be007a489a35d1199c25a483dc941ab2dcf5407edded23a430508f9d7ec87"
SPLIT = [129, 130, 131, 133, 134, 136, 137, 138, 139, 141, 145, 146]

print("generating seqcount base (636,754 vars) - this takes a few minutes",
      flush=True)
r = subprocess.run([PY, os.path.join(HERE, "cube_wave2.py"), "gen",
                    "seqcount", "12", "wave274"], cwd=HERE,
                   capture_output=True, text=True)
print(f"gen rc={r.returncode}", flush=True)
if r.returncode != 0:
    print(r.stdout[-2000:])
    print(r.stderr[-2000:])
    raise SystemExit(1)

rows = []
for mask in range(1 << len(SPLIT)):
    lits = [(SPLIT[i] if (mask >> i) & 1 else -SPLIT[i])
            for i in range(len(SPLIT))]
    rows.append("a " + " ".join(str(x) for x in lits) + " 0")
data = "\n".join(rows) + "\n"
with open(os.path.join(W, "cubes.txt"), "w", encoding="ascii",
          newline="\n") as fh:
    fh.write(data)

cubes_sha = hashlib.sha256(data.encode("ascii")).hexdigest()
with open(os.path.join(W, "base.cnf"), "rb") as fh:
    base_sha = hashlib.sha256(fh.read()).hexdigest()

m = json.load(open(os.path.join(W, "manifest.json"), encoding="ascii"))
m["cubes_sha256"] = cubes_sha
m["cube_construction"] = "mask-lsb-first.v1"
m["snapshot_commit"] = "54ca57814f25daf06a644efdea9ac4ec6a431c5c"
json.dump(m, open(os.path.join(W, "manifest.json"), "w", encoding="ascii",
                  newline="\n"), indent=1)

ok_b = base_sha == WANT_BASE
ok_c = cubes_sha == WANT_CUBES
print(f"n_vars    {m['base']['n_vars']}  expected 636754  "
      f"{m['base']['n_vars'] == 636754}")
print(f"n_clauses {m['base']['n_clauses']}  expected 1303901  "
      f"{m['base']['n_clauses'] == 1303901}")
print(f"n_cubes   {m['n_cubes']}  expected 4096  {m['n_cubes'] == 4096}")
print(f"base.cnf  {base_sha}")
print(f"          {'MATCH' if ok_b else '*** DIFFERENT from ' + WANT_BASE}")
print(f"cubes.txt {cubes_sha}")
print(f"          {'MATCH' if ok_c else '*** DIFFERENT from ' + WANT_CUBES}")
raise SystemExit(0 if (ok_b and ok_c) else 2)
