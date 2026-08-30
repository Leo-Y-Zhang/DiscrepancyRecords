# DiscrepancyRecords

A SAT-based campaign against `N(k,2)` - Erdos problem #176, OEIS A398541 - with
a cold verification gate that has to pass before this repo asserts anything.

**Status: campaign in progress. No new term is claimed.** Three claims are on
record: `N(3,2) = 9`, which is published; the published lower bound
`N(17,2) >= 273`, credited to its author below; and `N(17,2) >= 274`, which
rests on an avoiding coloring of `{1..273}` found here and re-checked by this
repository's own evaluator. No upper bound on `N(17,2)` is claimed at all. This
README will say otherwise only when `gate/verify_all.py` exits 0 on a claim that
says otherwise.

## The quantity

`N(k,l)` is the least `N` such that every coloring `f: {1..N} -> {-1,+1}` admits
a `k`-term arithmetic progression `P` (that is `a, a+d, ..., a+(k-1)d` inside
`{1..N}`, `d >= 1`) with `|sum_{n in P} f(n)| >= l`. A coloring of `{1..N}`
where every `k`-AP has `|sum| < l` **avoids** `(k,l)`, and proves
`N(k,l) >= N+1`. UNSAT of the avoidance instance at `N` proves `N(k,l) <= N`.

A398541 is `N(n,2)` with offset 2. Terms `a(2)..a(16)` are known. `a(17)` is
not, and that is the target of this campaign.

## What is here

- `nk2/` - AP enumeration in a fixed canonical order, a solver-free exact
  integer evaluator, witness I/O, a deterministic DIMACS writer, and **three
  independently written encoders** (subset, sequential counter, totalizer) that
  share no cardinality helper and must agree.
- `gate/verify_all.py` - the gate. It re-runs the arithmetic itself, regenerates
  each claimed instance and compares sha256, and requires two structurally
  different encodings behind any UNSAT claim. It runs with no solver installed.
- `tools/import_wave.py` - imports a finished off-repo cube wave as committable
  evidence: it refuses anything incomplete, re-derives the cube set from the
  split, consolidates the per-cube verdicts into one file, copies no proof, and
  prints the claim to paste in.
- `claims/` - what this repo asserts, and the evidence level of each assertion.
- `evidence/` - witnesses and run-logs, plus drat-trim transcripts once proofs
  are emitted; no transcript is on record yet, and every claim carries a null
  `drat` block. DIMACS instances and DRAT proofs are not committed; they are
  regenerated and hash-matched. A cube-and-conquer wave's manifest, per-cube
  verdicts and per-cube transcripts belong under `evidence/waves/`; no wave is
  on record, so that directory does not exist yet.
- `docs/PRD.md`, `docs/TDD.md` - why and how, written before the code.

## Running it

Python 3.13 and the packages in `requirements-dev.txt`. A SAT solver is optional
and is needed only to *search*, never to *verify*.

    pip install -r requirements-dev.txt
    ruff check .
    pytest -q -m "not solver"
    python gate/verify_all.py

The gate's exit code is the only claim this repository makes. It exits 0 on a
clean checkout with nothing installed beyond the above, and non-zero the moment
any claim is not fully supported by the artifacts on disk.

Tests marked `solver` need an external binary and are skipped without one. To
run them, and to search, point the driver at a solver directory - the variable
keeps machine-specific paths out of every committed file:

    NK2_SOLVER_DIR=/path/to/solver/bin pytest -q -m solver
    NK2_SOLVER_DIR=/path/to/solver/bin python -m nk2.solve \
        --N 9 --k 3 --encoder seqcount --solver kissat

`nk2.solve` writes the instance, records a run-log under `evidence/runs/`, and
prints the raw process return code. `rc=10` is SAT, `rc=20` is UNSAT, and
anything else is UNKNOWN - the solver's text output is never the verdict.

On Windows, note that the Microsoft Store `python` app-execution alias can hang
or return a permission error without ever launching an interpreter. Use a real
CPython 3.13 installation; `python -V` printing a version is the check.

## What the gate enforces

| Rule | What it refuses |
|---|---|
| G1 | A claim with an unknown key or an unknown kind. |
| G2 | A lower bound whose witness is missing, mis-hashed, the wrong length, or does not actually avoid - re-evaluated by the gate, never read from a stored flag. |
| G3 | An UNSAT claim backed by fewer than two encoders, by a run-log whose return code was not 20, or by an instance that does not regenerate to the recorded sha256. |
| G4 | A DRAT record whose transcript does not end `s VERIFIED` or does not match the instance G3 checked. |
| G5 | An anchor file that disagrees with the published terms, a claim that contradicts its anchor, or an exact term that is not contiguous with `a(16)`. |
| G6 | Any committed artifact holding an absolute path or a non-ASCII byte. |
| G7 | Any claim that declares more evidence than its artifacts support. |
| W1-W6 | A cube-and-conquer wave whose base instance does not regenerate, whose cube set is not every case, that has a cube missing or not returned UNSAT, whose transcripts do not match its verdicts, that is not about the instance the claim is about, or that is asked to carry an `exact` claim on one encoder. |

Each rule has a fixture that is asserted to make the gate exit non-zero.

## Cube-and-conquer waves

An upper bound at the scale this campaign is aiming for is not one solver run;
it is thousands of cubes, each the instance plus a few unit clauses. That turns
the gate's job from "did the solver say 20" into "are these cubes *every* case,
and did every one of them say 20". So a wave claim records a manifest - the
base instance's parameters and hash, the split variables, the cube count - and
one verdict per cube, and the gate re-derives the entire cube set from the split
and hashes it rather than reading a cubes file, because a wave that dropped a
case would write a shorter file and a hash that matches it. An `exact` claim
resting on a wave additionally needs a second complete wave from a different
encoder, or a monolithic run from one: a DRAT proof certifies that a CNF is
unsatisfiable and never that the CNF is the problem, so no amount of
proof-checking substitutes for a second encoding. **No wave claim is on record
in this repository. Nothing here says a wave has completed, and the gate is
where that will become visible if one ever does.**

A wave runs outside this repository - sixteen thousand cube instances and tens
of gigabytes of DRAT proofs are not a git tree - and `tools/import_wave.py` is
what brings the committable part across:

    python tools/import_wave.py --source <wave dir> --name k17_l2_N274_totalizer --dry-run

It checks the whole source before writing anything, refuses a wave that is
still running or has a cube that did not come back UNSAT, re-derives the cube
set from the split rather than believing the recorded hash, never touches the
source and never copies a proof, and prints the claim JSON to paste into
`claims/CLAIMS.json`. The verdicts land as one `verdicts.jsonl`, one per line:
committing sixteen thousand tiny files would make this repository slow to clone
and unreadable on GitHub. The gate reads that form and the one-file-per-cube
form identically - same reader, same rules - and in the consolidated form a
line that will not parse is a failure rather than a skip, because a reader that
skips one accepts a record that a dying machine truncated. A source may already
be consolidated, which is what a resumed campaign appends to; the verdicts are
written out sorted by cube with sorted keys whatever order they arrived in, so
two imports of one source are the same bytes.

What the tool will not do is import a wave at a level it cannot hold. A
verdict-only campaign keeps no proof and records no hash of one, so a transcript
quoting that same null is refused rather than written as `wave-drat-verified`
for the gate to then reject cube by cube. That wave imports without its
transcripts and stands honestly on its solver verdicts.

A wave is one directory. A verdict records a cube index, its literals, a
return code and a proof hash - it names no instance and no encoder, and the
literals follow from the split alone, so the verdicts of two encodings of the
same instance are byte-identical. Nothing inside them says which instance they
decided, so the gate takes the directory the manifest names,
`evidence/waves/<name>/`, as the wave, and reads that wave's verdicts,
transcripts and proofs only from inside it. Without that, a "second encoder"
could be a single `manifest.json` - which this repository's own code writes with
no solver in the room - nominating the first encoder's verdicts, and an `exact`
claim would pass on one encoding's work. That was found by a review of this
gate, and it is where a second pair of eyes earned its keep.

G2, G3 and G4 share one more rule: every path a claim or a transcript records
must be a plain repo-relative path to an artifact in the directory that kind of
artifact belongs in. A path that climbs out of the checkout, is absolute, lands
in a gitignored tree such as `scratch/`, or is a link out of the repository is
refused by the rule that read it - otherwise the gate could report as verified
an artifact that exists on one machine and in no checkout.

## Prior art, credited

- Spencer (1973) - the even-`k` parity formula `N(k,2) = 2^t*(k-1)+1` for
  `k = 2^t*m`, `m` odd.
- M. J. Goss, Jr. (2026) - values for odd `k`, Zenodo 10.5281/zenodo.20763838.
- T. A. Lystad - certificate bundles for small `k` and the current published
  lower bound `N(17,2) >= 273`, Zenodo 10.5281/zenodo.21840279 (v1.3).

This repo is an independent implementation written from the definition above.
It adopts that bundle's witness text format so artifacts interoperate, and
nothing else from it. The `N = 272` witness on record here was re-derived from
the published description - nine `+1` then eight `-1`, period 17, tiled 16 times
- and re-checked with this repo's own evaluator, which reports a maximum
`|AP sum|` of 1 across all 2176 seventeen-term APs in `{1..272}`.

## License

Proprietary source-available — see [LICENSE](LICENSE). You may read it, run it, and publish what you find, including a refutation. No reuse, modification, redistribution, or use as machine-learning training data. Author: Leo Y. Zhang.
