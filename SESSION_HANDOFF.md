# Session handoff

Updated: 2026-08-19 (mid-campaign), plus the 2026-08-24 block immediately below.

## STOP - READ BEFORE MOVING OR RENAMING THIS DIRECTORY (2026-08-24)

This clone's directory is named `DiscrepancyRecords_restored`, not
`DiscrepancyRecords`; both sit side by side under this machine's code root. The
plan on the daily board is to delete the old husk and rename this one onto its
proper name. **Do not do that while the campaign is running.** It is due to
finish Fri 28 Aug ~17:00.

Two independent reasons, both verified on 2026-08-24 rather than assumed:

1. **26 live processes are running out of this directory** - the orchestrator,
   its pool workers and 16 kissat solvers. Renaming a directory out from under
   them is the same class of act as the deletion that created this mess.

2. **The logon resume hook now points HERE by name.**
   `Startup\resume-erdos-campaign.cmd` runs `scratch\start_watchdog.ps1` inside
   this directory, naming it in full. It was repointed here after the deletion
   left it aimed at a script that no longer existed, which would have silently
   resumed nothing after a reboot. **Renaming the directory breaks it again, in
   the opposite direction.** Whoever renames it must repoint that hook in the
   same breath.

Nothing about the rename is urgent. The directory name costs nothing; the
campaign costs days. Copy the four surviving logs out of the old husk before
deleting it - `campaign.log` holds the phase 1 result and is the only record of
it.

Paths in this section are deliberately written relative. Gate rule G6 rejects an
absolute path in any tracked file: a machine path is unportable, and it is a
piece of this particular box's identity that has no business in a public repo.
The first version of this block carried three of them and turned the whole gate
red - `verify_all.py` stops at the first failure and reports "this repository
asserts nothing", so one handoff note took every claim down with it.

### Closed on 2026-08-24: both deferred pipeline defects

`check_pass.classify` is implemented. It routes every result to one of
transcript / skip-already-verified / checker-error / check-failure, re-reading
`transcripts.jsonl` on each call rather than caching, so a proof the pruner
reclaims after the pass started is a skip and not the false "a proof did not
verify" that halted the campaign on 23 Aug. The guard is narrow: an
`s NOT VERIFIED` or a sha mismatch still halts, even for a cube already in
transcripts. The `xfail(strict=True)` marker did its job first - every test
turned XPASS the moment `classify` existed, which pytest reports as a failure,
forcing the marker's own deletion.

`sample_prune`'s stand-down now banks its in-flight checks instead of discarding
them. `cancel_futures=True` cannot cancel a call already executing, and
`check_one` deletes the proof on success, so breaking out of the loop left a
deleted proof with no transcript line and no prune record. Draining is free:
`with ProcessPoolExecutor(...)` already waits for those calls on exit and simply
threw away what they returned.

Both were deferred because pool workers on Windows re-import their module on
spawn. That hazard is real but was not present: a process census found no live
`check_pass`, and under `--no-proof` the verdicts carry no `drat_sha256`, so
`sample_prune`'s check-batch path is never reached at all.

Still parked, and genuinely blocked: `scratch/cube_wave2.py` has one lint rule
in `per-file-ignores` because that file IS running, with sixteen pool workers.
Remove the block when the wave ends and re-run `ruff check .`.

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

The claim schema and the gate now speak cube waves: kind `upper_bound_wave`, an
optional `wave` block on any claim, and rules W1-W6 (manifest regenerates, cube
set re-derived and hashed, every cube rc 20, transcripts tied to verdicts, claim
and manifest about one instance, and no `exact` claim without a second encoder).
Mutation battery M28-M35 covers them, every mutant observed failing. No wave is
on record: `evidence/waves/` does not exist yet.

## Exact next step

When the wave completes: import the campaign evidence into
`evidence/waves/<name>/` - `manifest.json` (schema `cube-wave.v2`), one verdict
JSON per cube under `verdicts/`, and `transcripts.jsonl` with one checker line
per cube - plus the anchor run-logs for a(7)/a(9)/a(11), and write the claim as
`upper_bound_wave` at evidence level `unsat-wave` or `wave-drat-verified`. The
wave generator must cut its cubes by `nk2.cubes` (`mask-lsb-first.v1`, LSB
first, in the order `split_vars` lists) or W2 will refuse the manifest. Only
after the totalizer confirmation wave lands can the claim become `exact`; W6
fails an exact claim that has no `wave.confirm` naming a second encoder.

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
