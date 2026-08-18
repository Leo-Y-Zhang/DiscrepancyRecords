# PRD - DiscrepancyRecords: a gated SAT campaign for N(k,2)

**Status:** draft
**Date:** 2026-08-18 - **Repo:** DiscrepancyRecords - **Related:** docs/TDD.md

## Problem

Erdos problem #176 asks for `N(k,l)`, the least `N` such that every coloring
`f: {1..N} -> {-1,+1}` admits a `k`-term arithmetic progression `P` with
`|sum_{n in P} f(n)| >= l`. OEIS A398541 records `N(n,2)` and is known for
`a(2)..a(16)`. `a(17) = N(17,2)` is unknown; the best published lower bound is
`N(17,2) >= 273` (T. A. Lystad, Zenodo DOI 10.5281/zenodo.21840279, v1.3
bundle, periodic witness at `N = 272`). There is no tooling in this estate that
can attack that frontier, and - more importantly - no mechanism that makes a
claim about it *checkable by a stranger from the repo alone*. A campaign that
produces a number without a cold, re-runnable gate produces nothing.

## Who it is for

The repo owner, running a compute campaign alone, who must be able to answer
"why do you believe that number?" with a command rather than a memory. Secondly:
a reader of the eventual OEIS/writeup entry who has the repo and no context.

## Success looks like

- [ ] `python gate/verify_all.py` exits 0 on a clean checkout with **no SAT
      solver installed**, and exits non-zero the moment any claim in `claims/`
      is not fully supported by artifacts on disk.
- [ ] Three independently written encoders (subset, sequential counter,
      totalizer) provably agree, over an exhaustively enumerated matrix, with a
      solver-free integer evaluator that never uses the encoders' arithmetic.
- [ ] Every solver verdict on record carries the raw process return code. A
      verdict is never inferred from solver text output.
- [ ] The 15 published anchor terms `a(2)..a(16)` are pinned in `claims/` and
      cross-checked against the even-`k` parity formula by a test.
- [ ] A reproducible instance: the gate regenerates each claimed UNSAT instance
      from `(N, k, l, encoder)` and matches the recorded sha256 byte for byte.
- [ ] Any claim that overstates its own evidence level fails the gate.

## Requirements

**Must**
- `nk2/` package: AP enumeration, exact evaluator, witness I/O, deterministic
  DIMACS writer, three independent encoders, solver driver with run-logs.
- `gate/verify_all.py` as the sole arbiter of what this repo asserts.
- Test suite whose failures are *demonstrated*, not assumed (see TDD mutations).
- CI on ubuntu + windows, under two minutes, requiring no external solver binary.
- Honest labeling: lower bound, upper bound and exact are distinct claim kinds,
  and prior art is named per claim.

**Should**
- Optional sound symmetry breaking (`fix f(1) = +1`), off by default.
- DRAT proof emission and drat-trim transcript recording, when the tooling runs.

**Won't (this time)**
- Cube-and-conquer, distributed solving, or any parallel search orchestration.
- Local search / incremental solving for witness discovery beyond a plain
  solver call and periodic-coloring seeding.
- Any OEIS submission, Zenodo upload, publication, or outward claim of a new
  term. The repo may *hold* a supported claim; announcing it is a separate,
  owner-authorized act.
- `l != 2` campaign work. The code is general in `l`; the campaign is `l = 2`.

## Explicitly out of scope

- Reusing, vendoring or porting any code from the reference bundle. Only its
  witness *text format* is adopted, so artifacts interoperate. Every line here is
  written from the definition in this document, because the only value we add
  over the published bundle is an independent confirmation path.
- Beating the published `>= 273` lower bound is not the definition of success.
  A campaign that ends with "no improvement, here is the gated evidence of what
  we searched and the wall-clock we spent" is a successful outcome of this tool.
- App Flow and Design Brief: this is a CLI research tool with no UI, no screens
  and no human-facing state machine. Both documents would be empty ceremony.

## Safety and privacy

- **Personal data:** none. No accounts, no network calls, no user input beyond
  local file paths given on the command line.
- **Owner exposure is the real risk.** Committed artifacts must contain no
  absolute paths, no host name, no user name. The gate enforces this
  (rule G6) rather than leaving it to review.
- **Access revocation:** not applicable - no auth surface exists. If the repo is
  later made public, the irreversible act is *publication of a claim*, which is
  why the gate exists and why announcing is out of scope here.
- **Worst outcome if this is wrong:** an incorrect mathematical claim published
  under the owner's name, or a silently wrong encoder that makes a real UNSAT
  look reachable. Both are addressed by three-way encoder agreement against a
  solver-free evaluator, by requiring two structurally different encodings for
  every UNSAT claim, and by regenerating instances at gate time.
- Secondary: a multi-gigabyte DRAT proof committed by accident. `.gitignore`
  excludes `evidence/drat/`, `*.drat` and `evidence/**/*.cnf`; hashes and
  transcripts are committed, bulk artifacts are not.

## Open questions

These need the owner and do not change the module contracts below.

1. **Compute ceiling.** The analogous `k = 15, N = 225` instance took over three
   hours in one published monolithic run; `k = 17, N ~ 273` is roughly an order
   of magnitude larger (see TDD scale estimate). What single-run wall-clock and
   what total campaign budget is authorized before the effort is called?
2. **DRAT policy.** `drat-trim` is currently blocked locally by Smart App
   Control. Do we emit and hash proofs now (transcripts recorded when the tool
   runs), or defer proof objects entirely to a later phase?
3. **Outcome disposition.** If the campaign yields only an improved lower bound
   rather than `a(17)`, is that submitted anywhere, or held in-repo? (No action
   is taken either way without an explicit instruction.)

## Not doing / rejected alternatives

- **A single "best" encoding.** Rejected: one encoder cannot cross-check itself,
  and an UNSAT result from a single encoder is exactly the failure mode that
  cannot be caught after the fact.
- **Trusting solver stdout.** Rejected: absence of `s UNSATISFIABLE` in a
  truncated pipe is indistinguishable from a crash. The return code is the
  contract; anything outside `{10, 20}` is UNKNOWN.
- **Caching verdicts in the claim file.** Rejected: a cached "verified: true" is
  a promise, not evidence. The gate redoes the arithmetic every run.
- **Committing DIMACS instances.** Rejected: they are large and derived.
  Regeneration plus sha256 is strictly stronger evidence than storage.
