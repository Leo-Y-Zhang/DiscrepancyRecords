# Session handoff

Updated: 2026-08-19 (mid-campaign).

## Where this stands

The tooling is complete, adversarially tested (mutation battery M1-M27, every
mutant observed failing) and audited. Two claims are committed and gate-verified:
N(3,2) = 9 exact and N(17,2) >= 273 (prior art: Lystad, cited in the claim).

A witness for N = 273 exists and has passed the evaluator, so N(17,2) >= 274 is
established but NOT YET COMMITTED as a claim - it lands together with the
campaign evidence import (next step below).

A 4096-cube cube-and-conquer wave on the N = 274 seqcount instance (symmetry
break on, split on the 12 highest-occurrence variables, per-cube text DRAT) is
running off-repo, with each finished proof checked by Refute and reduced to a
sha256-bearing transcript line. A 64-cube presample returned 64/64 UNSAT.

## Exact next step

When the wave completes: import the campaign evidence (N = 273 witness, wave
manifest, per-cube transcripts, anchor run-logs for a(7)/a(9)/a(11)), extend the
claim schema and gate for cube-wave upper bounds (a wave is a manifest plus a
complete cube set plus one verified transcript per cube - G3/G4 are currently
single-run shaped), then run the totalizer confirmation wave for the two-encoder
rule before any exact claim for N(17,2) is written.

## Campaign policies (set during the 18-19 Aug overnight run; reversible)

- Compute ceiling: every solver invocation carries an EXTERNAL timeout
  (kissat 4.0.1's `--time=` option does not stop the solver). Waves run at
  3600 s per cube; monolithic probes at most 6 h. Two consecutive stalled
  nights on one target = stop and write a decision memo, not a third night.
- DRAT policy: per-cube text proofs (`--no-binary`); each proof is checked by
  Refute against the saved cube CNF, the transcript (sha256 of instance and
  proof, verdict, sizes) is committed, and the proof plus cube CNF are then
  deleted - both regenerate deterministically from the manifest.
- Lower-bound-only outcomes are committed as `lower_bound` claims (witness
  tier). They never extend OEIS DATA.

## Environment notes a future session will need

- Solvers: kissat 4.0.1 (`NK2_SOLVER_DIR` points at its directory when running
  `nk2.solve`). drat-trim is blocked by application control on this machine;
  Refute (the operator's own DRAT checker) is the working verifier.
- The four `solver`-marked tests skip without a solver; CI deselects them.
