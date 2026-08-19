# TDD - DiscrepancyRecords

**Status:** draft - **Date:** 2026-08-18 - **PRD:** docs/PRD.md

## Approach

`nk2/` turns `(N,k,l)` into an avoidance CNF three independent ways; a subprocess
driver runs a solver and records the raw return code; a solver-free integer
evaluator decides avoidance straight from the definition. `gate/verify_all.py` is
the only thing that speaks for this repo - it re-derives every claim from
artifacts on disk, regenerating instances and redoing the arithmetic itself.

`f: {1..N} -> {-1,+1}`; a `k`-AP is `a, a+d, ..., a+(k-1)d` inside `{1..N}`,
`d >= 1`; `f` **avoids** `(k,l)` iff every `k`-AP has `|sum_P f| < l`. Boolean
form `x_n <=> f(n)=+1`, `p = #plus(P)`, `sum_P f = 2p-k`. With
`u = ceil((k+l)/2)`, avoidance per AP is `k-u+1 <= p <= u-1`: **two at-most-`b`
constraints sharing `b = u-1`**, one over `[x_{p_1}..x_{p_k}]`, one over the
negated literals. **Independence rule (load-bearing):** `evaluator.py` works in
integers and never imports `spec.py` or an encoder, so a wrong `u` cannot hide -
equivalence tests compare encoders against arithmetic that never saw `u`. A test
asserts that boundary from the module source.

## Data model

No database. On-disk artifacts: ASCII, LF newlines. **`.gitattributes` pins
`eol=lf`** - `core.autocrlf` is true on the development machine, so without that
pin a committed witness would check out CRLF on Windows and LF on Linux and the
same artifact would hash to two different sha256 values, reddening G2 on one CI
job only. That was observed on this repo's first commit, not theorized.

| Path | Content | Committed |
|---|---|---|
| `claims/ANCHORS.json` | `{schema, sequence:"A398541", offset:2, terms:[15 ints], source}` | yes |
| `claims/CLAIMS.json` | claim objects (below) | yes |
| `evidence/witnesses/k<k>_l<l>_N<N>.txt` | witness text | yes |
| `evidence/runs/<name>.json` | one run-log per solve | yes |
| `evidence/transcripts/*.json` | drat-trim output + hashes | yes - **not** under `evidence/drat/`, which is ignored wholesale |
| `evidence/drat/*.drat` | DRAT proofs | no (gitignored) |
| `evidence/**/*.cnf` | DIMACS instances | no (regenerated, hash-matched) |

**Claim:** `{id, k, l, kind:"lower_bound"|"upper_bound"|"exact", value,
witness:{path,sha256}|null, unsat_runs:[path,...], drat:{proof_sha256,
proof_bytes, transcript}|null, evidence_level, prior_art, notes}`.
`lower_bound V` = `N(k,l) >= V`, needs an avoiding coloring of `{1..V-1}`;
`upper_bound V` = `N(k,l) <= V`, needs UNSAT at `N=V`; `exact V` needs both.
**Null cases to branch on rather than assume away** - they will occur: `witness`
null on an upper bound; `drat` null everywhere while proofs are deferred;
`unsat_runs` empty on a lower bound; a run-log with `rc: null` (timeout or
kill); a claim whose `k` has no anchor (`k >= 17`).

## Interfaces

`aps.py` - `num_aps(N,k)`: with `D=(N-1)//(k-1)`, `D*N - (k-1)*D*(D+1)//2`;
`k < 2` raises, `N < k` gives 0. `iter_aps(N,k)` yields in **canonical order**:
`d` ascending `1..(N-1)//(k-1)`, then `a` ascending `1..N-(k-1)*d`. That order
fixes CNF byte layout; it is contract, not convenience.

`evaluator.py` (imports neither `spec` nor encoders; integers only, no float) -
`max_abs_ap_sum(f) -> (int, ap|None)`, `avoids(f,k,l) -> bool`,
`first_bad_ap(f,k,l) -> ap|None` (first in canonical order). `f` holds `+1/-1`,
`f[n-1]` the value at `n`; anything else raises.

`spec.py` - `u_threshold(k,l) = -((k+l)//-2)` (integer ceil),
`avoid_bounds(k,l) -> (k-u+1, u-1)`; `lo > hi` means avoidance is impossible for
any `N >= k` and encoders emit the empty clause.

`witness.py` - `read_witness(path) -> (list[int], comments)`: `#` comment lines,
then exactly one line of `+`/`-`; any other character, a second data line or an
empty data line raises `WitnessFormatError`. `write_witness(path,f,k,l,
comments=(), verify=True)` **refuses to write a coloring that does not avoid
`(k,l)`**; header records `k`, `l`, `N` and the 1-indexed position convention.

`dimacs.py` - `write_cnf(path, n_vars, clause_iter) -> {n_vars, n_clauses,
sha256, path}`: streams clauses to a sibling temp file while counting, then
writes `p cnf <n_vars> <n_clauses>` and appends the body. Bytes: ASCII, `\n`
only (CRLF is the live Windows bug here), literals space-separated **in emission
order, not sorted**, each clause ends ` 0`, no comment lines, one trailing
newline; sha256 over exact file bytes.

Encoders - one signature, **no shared cardinality helper** (each hand-rolls its
own; sharing would destroy the diversity this design rests on):
`build(N,k,l,symmetry_break=False) -> (n_vars, Iterator[clause])` and
`num_vars(N,k,l)` closed form, since the header needs it before the body.

`solve.py` - `find_solvers()` probes `NK2_SOLVER_DIR` and `PATH` for `kissat`,
`cadical`; no machine path is ever committed. `solve(cnf, solver, timeout_s,
proof=None) -> RunLog`: **`rc==10 -> SAT`, `rc==20 -> UNSAT`, everything else
(0, 1, timeout, signal) -> `UNKNOWN`**. Text output is never the verdict; if the
`s` line contradicts `rc`, raise `SolverIntegrityError`. A model is parsed from
`v` lines only when `rc==10`, then re-checked with the evaluator before any
witness is written. Run-log: `{schema:"nk2.runlog.v1", instance:{path_rel,
sha256, n_vars, n_clauses, N, k, l, encoder, symmetry_break},
solver:{name,version,exe_sha256}, args, rc, verdict, timed_out, wall_seconds,
proof|null, host:{os,python}, started_utc, finished_utc}`, where `host.os` is
`platform.system()+release` only - no host name, no user name, no absolute path.

## Variable numbering (what makes regeneration possible)

Main variables **`var(x_n) = n`, `n = 1..N`** in all three encoders, so a model
decodes to a coloring with no lookup table. Aux vars sit in fixed-size blocks, so
any constraint's numbering is computable without generating those before it:
`base(t,c) = N + (2*t + c) * B(k,b)`, with `t` the 0-based AP index in canonical
order, `c=0` the positive-literal constraint and `c=1` the negated one, and
`b = u-1`. If `b <= 0` or `b >= k` then `B = 0` and the constraint is emitted as
units or omitted respectively.

- **subsets**: `B=0`, `num_vars = N`. Per AP, per `u`-subset `S` (in
  `itertools.combinations` order): clause `[-s for s in S]`, then
  `[+s for s in S]`. Raises `InstanceTooLarge` if `2*num_aps*C(k,u) > 5_000_000`.
- **seqcount** (Sinz): `B_seq = (k-1)*b`; register `s_{i,j}`, `i=1..k-1`,
  `j=1..b`, at `base + (i-1)*b + j`. Clause order: `(-L_1, s_{1,1})`;
  `(-s_{1,j})` for `j=2..b`; for `i=2..k-1`: `(-L_i, s_{i,1})`,
  `(-s_{i-1,1}, s_{i,1})`, then for `j=2..b` `(-L_i, -s_{i-1,j-1}, s_{i,j})` and
  `(-s_{i-1,j}, s_{i,j})`; last, `(-L_i, -s_{i-1,b})` for `i=2..k`.
- **totalizer** (truncated Bailleux-Boufkhad): balanced tree over the `k`
  literals in AP order, a node over `m > 1` leaves splitting `left = m//2`,
  `right = m - m//2`; output count `r = min(m, b+1)`, `O_j` = "at least `j`
  true"; blocks allocated **post-order** (left subtree, right subtree, this
  node), `var(O_j) = node_base + j - 1`; size `B_tot(k,b) = T(k)` where
  `T(m) = 0` for `m <= 1` else `T(m//2) + T(m-m//2) + min(m, b+1)`. Per internal
  node: for `alpha=0..p`, `beta=0..q` with `1 <= alpha+beta <= r`, clause
  `(-A_alpha, -B_beta, O_{alpha+beta})`, zero-index terms dropped; the root takes
  unit `(-O_{b+1})` when `b+1 <= k`. Aux count, implication graph and clause
  widths all differ from the counter chain - the diversity is structural.

Emission order: APs canonical, `c=0` before `c=1`, clauses as listed. Symmetry
break, when on, appends exactly one clause `1 0` - sound because avoidance is
invariant under `f -> -f`; documented at the flag, asserted by a test.
**Target scale:** `k=17, N=273` gives 2193 APs, seqcount `B = 16*9 = 144` per
constraint, so ~632k vars and ~1.3M clauses; totalizer fewer vars, similar
clauses; subsets ~85M clauses, correctly refused by its guard.

## Access control

No auth, database, RLS, definer function or listener; no grant exists to revoke.
Three trust boundaries: CLI paths (resolved, required under the repo root before
any write), solver output (trusted for a model only when `rc==10`), claim files
(schema-checked, unknown keys rejected, and every recorded path resolved under
the root before it is read - see the gate's path rule below).

## The gate - `gate/verify_all.py`

Runs with **no solver installed**, trusts no cached verdict, exits 0 iff every
rule passes; failures print `FAIL <rule> <claim-id> <reason>`.

| # | Rule |
|---|---|
| G1 | Every claim parses against the schema; unknown kind or unknown key fails. |
| G2 | Lower bound `V`: witness exists, sha256 matches, parses, length `== V-1`, and **the gate itself runs `evaluator.avoids`** - no stored verified-flag is ever read. |
| G3 | Upper bound `V`: two or more run-logs with `verdict=="UNSAT"` **and `rc==20`** at `(N=V,k,l)`, from **two distinct encoders**; for each, the gate regenerates the instance from the recorded parameters and requires a sha256 match. |
| G4 | If `drat` present: proof sha256 and byte count match (an absent proof merely makes the level unreachable, and G7 catches the overstatement), transcript ends `s VERIFIED`, transcript instance sha256 equals G3's. drat-trim re-runs only under `--reverify-drat` when the binary exists. |
| G5 | `ANCHORS.json` equals the 15 published terms held as a literal in the gate; every claim with `k <= 16` is consistent with its anchor; an `exact` claim for `k > 17` fails as non-contiguous with `a(16)`. |
| G6 | No committed artifact holds an absolute path (`[A-Za-z]:[\\/]`, `/home/`, `/Users/`) or a non-ASCII byte. |
| G7 | Achieved evidence level `>=` declared `evidence_level`; overstatement fails, understatement prints INFO. Levels: `witness` < `unsat-dual` < `drat-transcript` < `drat-reverified`. |

**Path rule, shared by G2, G3 and G4.** Every path recorded in a claim or in a
transcript is resolved with a containment check, never by joining it to the
root. It must be a plain repo-relative path (no drive letter, no leading
separator, no `..`, no backslash); it must sit under the directory its kind
belongs in - `evidence/witnesses/`, `evidence/runs/`, `evidence/transcripts/`,
and merely under `evidence/` for the gitignored bulk an instance or a proof is;
a committed artifact must not carry a gitignored suffix (`.cnf`, `.drat`); and
it must still be inside the root once symlinks and junctions are resolved.
Without this a claim can point at a file that is on one machine and in no
checkout - `../elsewhere/witness.txt`, or `scratch/witness.txt` - and the gate
prints "verified from artifacts on disk" for a repository that holds no
evidence at all. That is the precise deception the gate exists to prevent, so
it is a failure under the rule that reads the path, not a warning.

Artifacts referenced by no claim are WARN only - untidiness, not unsoundness.

## Failure modes

| What breaks | Who notices | How we detect it | How we undo it |
|---|---|---|---|
| Encoder bug makes a satisfiable instance UNSAT | nobody, until it is public | three-way equivalence tests; G3's two-encoder rule | revert encoder; gate reddens on that claim |
| Solver killed (OOM, SAC), `rc=1` | operator | rc outside {10,20} -> UNKNOWN, no claim written | re-run; nothing to undo |
| Numbering drifts after a refactor | CI | golden sha256 per encoder + G3 regeneration | revert, or re-solve and re-record |
| CRLF newlines on Windows, from the writer or from git checkout | CI windows job | golden sha256 test; witness sha256 in G2 | fix the writer; keep `.gitattributes eol=lf` |
| DRAT proof fills the disk | operator, at once | proofs opt-in, gitignored, size recorded | delete `evidence/drat/`; claim drops to `unsat-dual` |
| Claim overstates its evidence | gate | G7 | correct the declared level |
| Anchor transcription error | CI | parity-formula test + G5 double copy | correct both copies |

## Rollback

Fully reversible: `git revert`, or delete the tree. No database, deploy,
migration or external side effect; CI runs tests and publishes nothing. Evidence
regenerates except witnesses, cheap to re-find or re-copy from the cited public
bundle. The one irreversible act in this project's life is publishing a claim
outward - out of scope here, and the owner's alone.

## Test plan

`tests/`, pytest, marker `solver` for anything needing an external binary
(skip-if-absent); `tests/_minisolve.py` is a <=60-line UP + DPLL checker deciding
whether a partial assignment extends.

**Positive** - `num_aps` equals `len(list(iter_aps))` for all `N <= 40`,
`k = 2..8`, output strictly ordered and inside `{1..N}`; the evaluator says
`--++--++` avoids `(3,2)` (so `N(3,2) >= 9`) and the published `k=17, N=272`
periodic coloring (9 plus, 8 minus, x16) avoids `(17,2)` - an independent
re-check of the cited lower bound; witness round-trip is identity including
comments; parity `N(k,2) = 2^t*(k-1)+1` matches `ANCHORS.json` at every even `k`
in 2..16; end-to-end via `python-sat` gives `k=3,N=8` SAT and `k=3,N=9` UNSAT for
**each** cardinality encoder, the model decoding to a coloring the evaluator
confirms; the gate exits 0 on the good fixtures.

**Negative** - three-way equivalence: for `(k,l)` in
`{(3,2),(3,3),(4,2),(5,2),(6,2),(4,1),(5,1)}` and `N` up to 12, enumerate all
`2^N` colorings; the evaluator's avoid-set must equal exactly the set of
colorings extending to a model of each encoder, and for both cardinality encoders
unit propagation alone must decide each fixed-input case (dropping a clause
breaks that). Single-constraint extension: `n <= 10`, every `b` in `0..n`, all
`2^n` assignments extend iff `popcount <= b`, seqcount and totalizer separately.
A stub solver printing `s UNSATISFIABLE` with exit 0 must yield `UNKNOWN`; a stub
whose `s` line contradicts `rc` must raise. Bad-claim fixtures, one per gate
rule, each asserted to make the gate exit non-zero: flipped sign in a witness;
witness of length `V` not `V-1`; single-encoder UNSAT; `verdict UNSAT` with
`rc 1`; sha mismatch; non-contiguous `k=19` exact claim; overstated
`evidence_level`; absolute path in a claim file. The witness reader rejects `0`,
whitespace inside the data line, two data lines, and a comments-only file.
Path rule, each against a copy of the good fixture whose artifact is genuine and
only mislocated: a witness or run-log path that climbs out of the root with
`..`, one that lands in the gitignored `scratch/`, an absolute one, a committed
one carrying a gitignored suffix, a transcript outside `evidence/transcripts/`,
a proof or instance path out of the tree, and a witness directory that is a link
to somewhere else. Each must fail under the rule that read it, and a good DRAT
block in the right directories is the positive control beside them.

**Boundary** - `N < k` (no APs): zero clauses, header `p cnf N 0`, evaluator says
avoids; `l=1` odd `k`: `lo > hi`, empty clause, UNSAT, `N(k,1) = k`; `b=0`,
`b=k-1`, `b>=k` in both cardinality encoders; `k=2`; `d` and `a` each at maximum;
symmetry break gives model count `>0` iff `>0` without it, and the default build
has no unit clause on variable 1; golden sha256 per encoder on `(9,3,2)` and
`(13,5,2)`, with and without the break, committed as a regression pin;
import-boundary test on `evaluator.py` source.

**Mutations that must turn the suite red** (the tester phase executes these - a
test never observed failing is decoration):

| # | Mutation | Must fail |
|---|---|---|
| M1 | `avoids`: `>= l` -> `> l` | parity/anchor + spot checks |
| M2 | `iter_aps`: only `d=1` | `num_aps`; `k=3,N=9` UNSAT anchor; the `d>=2` evaluator case |
| M3 | `iter_aps`: drop the last `a` per `d` | `num_aps`; max-`a` evaluator case |
| M4 | `u_threshold`: ceil -> floor | `k=3,N=8` SAT anchor; equivalence |
| M5 | seqcount bound `b` -> `b+1` | equivalence; single-constraint extension |
| M6 | totalizer: drop root unit `(-O_{b+1})` | equivalence; single-constraint |
| M7 | totalizer split `m//2` -> `1` (chain) | golden sha256; `num_vars` |
| M8 | dimacs writer emits CRLF | golden sha256 (both jobs) |
| M9 | header `n_vars = N`, ignoring aux | DIMACS validity (`abs(lit) <= n_vars`) |
| M10 | gate skips the evaluator re-check | flipped-sign witness fixture |
| M11 | gate accepts one encoder | single-encoder fixture |
| M12 | gate reads `verdict` without `rc==20` | `rc 1` fixture |
| M13 | gate skips instance regeneration | sha-mismatch fixture |
| M14 | gate allows a non-contiguous term | `k=19` fixture |
| M15 | gate ignores the declared level | overstated-level fixture |
| M16 | witness reader maps unknown chars to `-` | reader rejection tests |
| M17 | `solve` infers a verdict from stdout | stub-solver test |
| M18 | symmetry break on by default | golden hash + no-unit assertion |
| M19 | subsets uses `u-1`-subsets | equivalence |
| M20 | parity helper uses `m` where `k` belongs | parity-vs-anchors test |
| M21 | gate joins a recorded path to the root with no containment check | every path-rule test |
| M22 | gate drops the absolute / drive-letter check | absolute-witness test |
| M23 | gate drops the `..` check | escaped witness, run-log and proof tests |
| M24 | gate drops the required-directory check | the `scratch/` tests |
| M25 | gate drops the gitignored-suffix check | witness named `.cnf` |
| M26 | gate drops link resolution | linked-out witness directory |
| M27 | gate drops the one-spelling rule | `./evidence/...` witness path |

## CI and environment

`.github/workflows/ci.yml`: `ubuntu-latest` + `windows-latest`, Python 3.13, job
timeout 10 minutes, target under two. Install `pytest` (unpinned runner),
`python-sat` (CI-only SAT engine for the tiny anchors) and a pinned `ruff` from
`requirements-dev.txt`; run `ruff check`, `pytest -q -m "not solver"`, then
`python gate/verify_all.py`. **No external solver binary is required by CI** -
the gate must be cold-runnable, and that is the point.

Measured 2026-08-18: **Python would not launch at all during design** -
`Permission denied` from the WindowsApps alias under git-bash,
`ApplicationFailedException` under PowerShell, and the versioned
`PythonSoftwareFoundation.Python.3.13_*` executable hanging past 120 s then
returning `The specified disk or diskette cannot be accessed.`, which is a broken
Store app-execution alias rather than a slow scan. **Coder's first action, before
any code:** get one real `python -V` and `pytest --version`; retry first (the
fleet has seen this clear in minutes), else fall back to a user-scope `winget`
Python, as MSI installs are impossible here; record the working invocation in the
README and write no `nk2/` code until a runner has been seen running. Also:
`kissat.exe` and `kissat-assert.exe` exist but **no cadical binary does**, so
`find_solvers` returns kissat only - encoding diversity, not solver diversity,
carries the UNSAT claims; and `drat-trim.exe` exists but is Smart-App-Control
blocked, so every drat test is `@pytest.mark.solver` and skips when it will not
run.

## Build order

1. Skeleton: `LICENSE` (MIT, `Leo Y. Zhang`), `requirements-dev.txt` with a
   pinned `ruff`, `nk2/__init__.py`, empty `claims/` and `evidence/` trees.
2. `aps.py` + tests (closed form vs enumeration) - nothing works without
   canonical AP order.
3. `evaluator.py` + spot checks including the `k=17, N=272` re-check.
4. `witness.py` (round-trip, rejections), then `spec.py` and `dimacs.py`
   (golden-hash, validity).
5. `encode_subsets.py`, `encode_seqcount.py`, `encode_totalizer.py`, each with
   single-constraint tests before the shared equivalence matrix; then the matrix
   and the import-boundary test.
6. `solve.py` + stub tests, then one real kissat run on `k=3, N=9`.
7. `claims/ANCHORS.json` and `claims/CLAIMS.json`, seeded with the published
   `N(17,2) >= 273` lower bound citing the Zenodo DOI and the re-checked `N=272`
   witness.
8. `gate/verify_all.py` + good and bad fixtures, one per rule.
9. CI workflow; confirm both jobs green and inside the time budget.
10. `README.md` from stub to reality, with the working run commands.

## Open questions

Carried from the PRD, unchanged by this design: the compute ceiling for a single
run, whether DRAT proof objects are emitted in this phase, and the disposition of
a lower-bound-only outcome. None alters a module contract, a file format or a
gate rule - they set budget and what gets recorded, not what is correct.
