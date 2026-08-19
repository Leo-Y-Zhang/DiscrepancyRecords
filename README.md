# DiscrepancyRecords

A SAT-based campaign against `N(k,2)` - Erdos problem #176, OEIS A398541 - with
a cold verification gate that has to pass before this repo asserts anything.

**Status: campaign in progress. No new term and no improved bound are claimed.**
The two claims currently on record are `N(3,2) = 9`, which is published, and the
published lower bound `N(17,2) >= 273`, which is credited to its author below.
This README will say otherwise only when `gate/verify_all.py` exits 0 on a claim
that says otherwise.

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
- `claims/` - what this repo asserts, and the evidence level of each assertion.
- `evidence/` - witnesses, run-logs and proof transcripts. DIMACS instances and
  DRAT proofs are not committed; they are regenerated and hash-matched.
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

Each rule has a fixture under `tests/fixtures/` that is asserted to make the
gate exit non-zero.

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

MIT. Author: Leo Y. Zhang.
