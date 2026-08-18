# DiscrepancyRecords

A SAT-based campaign against `N(k,2)` - Erdos problem #176, OEIS A398541 - with
a cold verification gate that has to pass before this repo asserts anything.

**Status: campaign in progress. Nothing is claimed here yet.** No new term, no
improved bound, no result. This README will say otherwise only when
`gate/verify_all.py` exits 0 on a claim that says otherwise.

## The quantity

`N(k,l)` is the least `N` such that every coloring `f: {1..N} -> {-1,+1}` admits
a `k`-term arithmetic progression `P` (that is `a, a+d, ..., a+(k-1)d` inside
`{1..N}`, `d >= 1`) with `|sum_{n in P} f(n)| >= l`. A coloring of `{1..N}`
where every `k`-AP has `|sum| < l` **avoids** `(k,l)`, and proves
`N(k,l) >= N+1`. UNSAT of the avoidance instance at `N` proves `N(k,l) <= N`.

A398541 is `N(n,2)` with offset 2. Terms `a(2)..a(16)` are known. `a(17)` is
not, and that is the target of this campaign.

## What is here

- `nk2/` - AP enumeration, a solver-free exact integer evaluator, witness I/O, a
  deterministic DIMACS writer, and **three independently written encoders**
  (subset, sequential counter, totalizer) that must agree.
- `gate/verify_all.py` - the gate. It re-runs the arithmetic itself, regenerates
  each claimed instance and compares sha256, and requires two structurally
  different encodings behind any UNSAT claim. It runs with no solver installed.
- `claims/` - what this repo asserts, and the evidence level of each assertion.
- `evidence/` - witnesses, run-logs and proof transcripts. DIMACS instances and
  DRAT proofs are not committed; they are regenerated and hash-matched.
- `docs/PRD.md`, `docs/TDD.md` - why and how, written before the code.

## Prior art, credited

- Spencer (1973) - the even-`k` parity formula `N(k,2) = 2^t*(k-1)+1` for
  `k = 2^t*m`, `m` odd.
- M. J. Goss, Jr. (2026) - values for odd `k`, Zenodo 10.5281/zenodo.20763838.
- T. A. Lystad - certificate bundles for small `k` and the current published
  lower bound `N(17,2) >= 273`, Zenodo 10.5281/zenodo.21840279 (v1.3).

This repo is an independent implementation written from the definition above.
It adopts that bundle's witness text format so artifacts interoperate, and
nothing else from it.

## Running it

Requires Python 3.13 and pytest; a SAT solver is optional and is needed only to
search, never to verify.

    pytest -q -m "not solver"
    python gate/verify_all.py

The gate's exit code is the only claim this repository makes.

## License

MIT. Author: Leo Y. Zhang.
