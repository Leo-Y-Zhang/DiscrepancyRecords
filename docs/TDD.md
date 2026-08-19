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
| `evidence/waves/<name>/manifest.json` | one cube-wave manifest (below) | yes |
| `evidence/waves/<name>/verdicts.jsonl` | every verdict, one per line | yes |
| `evidence/waves/<name>/verdicts/*.json` | the same verdicts, one file each | yes |
| `evidence/waves/<name>/transcripts.jsonl` | one checker line per cube | yes |
| `evidence/waves/<name>/proofs/*.drat.gz` | per-cube DRAT, compressed | no (gitignored) |

**A wave's verdicts come in either of two forms and the claim says which.** A
`verdicts_dir` ending `.jsonl` is one file holding one verdict object per line;
anything else is a directory of one file per cube. Both hold the same objects
and every W rule holds identically over both - the reader is one function, so
"a duplicate cube is refused" cannot be true of one form and false of the other.
The consolidated form exists because the live wave is 16384 cubes: committing
that many files makes a repository slow to clone and impossible to read on
GitHub, and reviewing a diff of it is not a thing anybody would do. The form is
taken from the *recorded name* and never from what is on disk, so one claim
reads one way and a directory that has been given a `.jsonl` name is refused
rather than quietly accepted.

**Claim:** `{id, k, l, kind:"lower_bound"|"upper_bound"|"upper_bound_wave"|
"exact", value, witness:{path,sha256}|null, unsat_runs:[path,...],
drat:{proof_sha256, proof_bytes, transcript}|null, wave:{...}|null,
evidence_level, prior_art, notes}`.
`lower_bound V` = `N(k,l) >= V`, needs an avoiding coloring of `{1..V-1}`;
`upper_bound V` = `N(k,l) <= V`, needs UNSAT at `N=V` from two encoders;
`upper_bound_wave V` is the same bound carried by a cube-and-conquer wave;
`exact V` needs both sides.
**Null cases to branch on rather than assume away** - they will occur: `witness`
null on an upper bound; `drat` null everywhere while proofs are deferred;
`unsat_runs` empty on a lower bound or on a wave-carried bound; a run-log with
`rc: null` (timeout or kill); a per-cube verdict with `rc: null` for the same
reason; a verdict with **no `drat_sha256` and no `drat_bytes` key at all**, or
with the pair present and null, when that cube was solved without a proof; a
claim whose `k` has no anchor (`k >= 17`).

**`wave` is the one optional key.** Every claim written before waves existed
omits it, and absent means null. That asymmetry is deliberate and safe in one
direction only: a missing wave can lower a claim's evidence, never raise it. Any
key not in the schema is still refused, and every other key is still required.

## Cube-and-conquer waves

A wave decides one instance by deciding `2**s` derived ones - the base CNF plus
`s` unit clauses fixing the split variables - so it proves the base UNSAT **iff
the cube set is every assignment of the split.** That "iff" is the entire
soundness argument, and it is exactly the part a cubes file cannot be trusted
for: a generator that dropped a case writes a shorter file and a matching hash.
So the gate never reads a cube file. `nk2/cubes.py` re-derives the whole set
from `split_vars` and hashes that; the manifest's `cubes_sha256` has to equal
what falls out.

**Construction** `mask-lsb-first.v1`: for cube `i`, literal `j` is
`+split_vars[j]` when bit `j` of `i` is set and `-split_vars[j]` when it is not,
least significant bit first. The cube file is one `a <lits> 0` line per cube in
index order, ASCII, LF, one trailing newline. A cube instance is the base CNF
with those literals appended as units and the header clause count raised by `s`
- `write_cube_cnf` is the only implementation, pinned by a test against
`dimacs.write_cnf` fed the same clauses. Between them, the manifest's
`base.sha256` and the construction fix the exact bytes of every cube instance
without a single one of them being stored, which is what lets the gate demand
regeneration for a wave exactly as G3 does for a monolithic run.

**Manifest** `{schema:"cube-wave.v2", N, k, l, encoder, symmetry_break,
snapshot_commit, base:{n_vars,n_clauses,sha256}, split_vars:[int], n_cubes,
cubes_sha256, cube_construction}`. **Verdict**, one per cube, as a file or as a
line: `{cube, lits, rc, wall_s}` plus **optionally** `{drat_sha256,
drat_bytes}`. **Transcript**, one JSONL line per cube: `{cube, drat_sha256,
drat_bytes, proof_path_rel, checker, verdict}`.

**The two proof keys are optional per cube, and a wave may be mixed.** The
driver's `--no-proof` mode - which is how a second-encoder *confirmation* wave
is run, since the cheapest honest confirmation is a second encoding rather than
a second proof - writes `{cube, lits, rc, wall_s}` and stops: there was no
proof, so there is no digest, and leaving the pair out is the record of what
happened rather than a defect. A campaign interrupted with proofs on and
resumed with them off holds both shapes at once, and that is legal. This was
not theorized: `scratch/wave274` holds 75 four-key verdicts beside 42 six-key
ones, and a reader demanding six keys refused the wave the tool exists to
import. Absent and null both mean *no proof hash on record*, and both can only
ever lower what a wave is worth - see the tier rule below, which is what keeps
that safe. Every other key is still required, and any key not in the schema is
still refused.

**Claim block** `wave: {manifest, verdicts_dir, transcripts:path|null,
confirm:null | {kind:"wave", manifest, verdicts_dir, transcripts} |
{kind:"unsat_runs"}}`. `verdicts_dir` names the directory of verdicts or the
consolidated `.jsonl`; the key keeps its name because renaming a schema key
that is already read by the gate buys nothing a sentence here does not. A
confirm of kind `unsat_runs` points at the claim's own `unsat_runs` and asks the
gate to find one there from another encoder.

**A wave is one directory, and the manifest is the file that names it.** A
verdict records `{cube, lits, rc, wall_s, drat_sha256, drat_bytes}` and nothing
else: no N, no encoder, no instance hash. `lits` is a function of `split_vars`
alone, and `cubes_sha256` likewise, so two waves that share a split - which
every encoder of one instance does, since `var(x_n) = n` - write byte-identical
`lits` for every cube. **Nothing inside a verdict binds it to the instance it
decided.** The only thing that can is where it sits. So `wave.manifest` must be
exactly `evidence/waves/<name>/manifest.json`, and `verdicts_dir`, `transcripts`
and every `proof_path_rel` must be strictly inside that `<name>/` directory.
Pinning the file name is what makes one directory mean one wave: two manifests
in a directory could otherwise nominate the same verdicts and satisfy
containment while doing it. Without the rule, W6 is satisfiable by a single
`manifest.json` - which `nk2` writes with no solver in the room - whose
`verdicts_dir` points at the first encoder's verdicts, and an `exact` claim
passes at `drat-transcript` on one encoder's solving. That was found by the
tester, not theorized; it is regression-tested in `tests/test_gate_waves.py`
under "a wave is one directory".

**Three things the gate cannot check, recorded so nobody mistakes them for
checked.** `snapshot_commit` is required to be a well-formed commit id and is
never treated as evidence: the gate runs on a checkout that need not be a git
repository. `drat_sha256` is the sha256 of the *uncompressed* proof, so while a
`.drat.gz` sits on disk uncompressed its bytes are tied to nothing the gate
reads - `--reverify-drat` is where a proof is decompressed, hashed against the
transcript and fed to a checker. And a wave manifest records no solver identity
at all, where a monolithic run-log records name, version and exe sha256.

The verdict schema is the load-bearing one of the three. Because a verdict names
no instance, **verdict files physically copied from one wave's directory into
another's are indistinguishable from honest ones**, and the directory rule above
does not change that: it closes the accident (a pasted claim block keeping the
previous wave's `verdicts_dir`) and the one-file forgery, not a deliberate copy.
Two tempting cross-wave checks are unsound and were rejected: requiring the two
waves' `drat_sha256` to differ per cube fails an honest pair of proofless waves,
where both are null, and can fail a genuine pair where a trivially refuted cube
yields the same tiny proof under both encodings; requiring the verdict *sets* to
differ rests on wall-clock noise, which a copier can perturb. Closing it needs a
`cube-wave.v3` verdict carrying the base instance sha256 and the encoder, so
that a verdict states which instance it decided and W3 can regenerate that and
compare. The live wave emits v2, so this is written down rather than shipped.

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

`cubes.py` - `cube_literals(split_vars,i)`, `cube_clauses`, `cubes_text`,
`cubes_sha256` and `write_cube_cnf(base_cnf, split_vars, i, out)`; `CONSTRUCTION`
names the version above and `check_split` refuses a split that repeats a
variable, holds a non-variable, is empty, or exceeds 24 variables. It imports no
encoder: the construction is over variable numbers, and `var(x_n) = n` is what
makes a split mean the same thing to two different encodings.

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
| G7 | Achieved evidence level `>=` declared `evidence_level`; overstatement fails, understatement prints INFO. Levels below. |
| W1 | The manifest is `evidence/waves/<name>/manifest.json` - **that directory is the wave**, and W3 and W4 read nothing from outside it - parses, has exactly the `cube-wave.v2` keys, and its base instance **regenerates** from `(N,k,l,encoder,symmetry_break)` to the recorded sha256, var count and clause count - the same machinery G3 uses. `split_vars` are distinct main variables in `1..N`; `n_cubes == 2**len(split_vars)`. |
| W2 | The cube set is complete **by construction**: the gate re-derives every cube from `split_vars` and hashes the result against `cubes_sha256`. No cubes file is read, and an unrecognised `cube_construction` fails rather than being guessed at. |
| W3 | The verdicts are read from inside the wave's own directory, from the directory or the `.jsonl` the claim names. Every cube id `0..n_cubes-1` has exactly one verdict, each with `rc == 20` - any other value, `null` included, is UNKNOWN and leaves the decomposition open - and each verdict's `lits` are the construction's literals for that id. `cube`, `lits`, `rc` and `wall_s` are required; the two proof keys are optional and a wave may be mixed. A second verdict for one cube fails, a cube id outside the range fails, and in the consolidated form **a line that is not a verdict is a failure and never a skip** - a reader that skips one passes a wave whose record is short by however many lines a dying machine truncated. |
| W4 | If `transcripts` is present: it and every proof it names sit inside the wave's own directory; **one line per cube of the wave**, each line's `drat_sha256` a real 64-hex digest equal to that cube's verdict digest - so a cube whose verdict recorded none cannot be covered - `drat_bytes` equal with it, `verdict` exactly `s VERIFIED`, a checker named, and a `proof_path_rel` ending `.drat.gz`. Coverage of only some cubes fails; it does not downgrade. The gate does not decompress a proof; that is the checker pass. |
| W5 | The claim and the manifest are about one instance: `value == manifest.N`, `k` and `l` equal. `upper_bound_wave` needs a wave; `lower_bound` and plain `upper_bound` may not carry one; `exact` needs both sides - a witness at `V-1` and a wave (or two-encoder `unsat_runs`) at `V`. |
| W6 | **No exact claim rests on one encoding.** An `exact` claim carrying a wave needs `wave.confirm`: a second complete wave from a *different* encoder (transcripts optional, W1-W3 checked just as hard), or `unsat_runs` holding a verified run-log from a different encoder. Absent that, the claim fails outright - declaring a lower `evidence_level`, or having the lower side on record, does not buy the word "exact". Confirmation is what lifts a wave to `unsat-dual` and above. |

**Evidence levels**, weakest to strongest:
`witness` < `unsat-wave` < `wave-drat-verified` < `unsat-dual` <
`drat-transcript` < `drat-reverified`. The two wave tiers are new, and where
they sit is a judgement worth stating. A wave carries one encoder's opinion of
what avoidance means, however completely it is decomposed and however
thoroughly each cube is proof-checked: a DRAT proof certifies that a CNF is
unsatisfiable, never that the CNF is the problem. Encoder diversity is what this
repository's soundness argument rests on, so `wave-drat-verified` sorts **below**
`unsat-dual` - 16384 checked proofs of a wrong encoding are 16384 checked proofs
of the wrong thing. A wave reaches `unsat-wave` bare, `wave-drat-verified` with
full transcripts, `unsat-dual` when a second encoder confirms it, and
`drat-transcript` with both.

**The tier rule is what makes optional proof keys safe.**
`wave-drat-verified` requires transcripts covering **every** cube of the wave,
each carrying a real 64-hex sha256 that equals that cube's verdict digest. A
wave with no transcripts, or one whose verdicts kept no digests, therefore
cannot reach it however it is declared: nothing was checked, so the tier that
says everything was checked is an overstatement and G7 refuses it. A wave with
transcripts over only part of itself is a *failure*, not a downgrade - see the
import section, where the same shape is refused a step earlier and for the same
reason. G7's semantics are unchanged: the declared level is
a floor on strength, so understating it is INFO and overstating it fails. There
is no wave-reverified tier - `--reverify-drat` over a wave is a check that can
fail, not a promotion.

**Path rule, shared by G2, G3, G4 and W1-W4.** Every path recorded in a claim,
a transcript or a wave block is resolved with a containment check, never by
joining it to the root. It must be a plain repo-relative path (no drive letter,
no leading separator, no `..`, no backslash); it must sit under the directory
its kind belongs in - `evidence/witnesses/`, `evidence/runs/`,
`evidence/transcripts/`, `evidence/waves/`, and merely under `evidence/` for the
gitignored bulk an instance or a proof is;
a committed artifact must not carry a gitignored suffix (`.cnf`, `.drat`, `.gz`
- a compressed proof is `cube00000.drat.gz`, whose suffix is `.gz`); and
it must still be inside the root once symlinks and junctions are resolved.
**For a wave the directory a path belongs in is the wave's own** - the one its
`manifest.json` names, not `evidence/waves/` at large - so a verdicts directory
or `.jsonl`, a transcripts file or a proof belonging to a different wave is
refused under the rule that read it: W1 for the manifest, W3 for the verdicts,
W4 for the transcripts and the proofs.
Without this a claim can point at a file that is on one machine and in no
checkout - `../elsewhere/witness.txt`, or `scratch/witness.txt` - and the gate
prints "verified from artifacts on disk" for a repository that holds no
evidence at all. That is the precise deception the gate exists to prevent, so
it is a failure under the rule that reads the path, not a warning.

Artifacts referenced by no claim are WARN only - untidiness, not unsoundness. A
wave is referenced as a unit: naming the manifest covers the directory it sits
in, because listing sixteen thousand verdict files in a claim would serve
nobody.

## Importing a wave - `tools/import_wave.py`

A wave runs off-repo, because 16384 cube instances and tens of gigabytes of
compressed DRAT are not a git tree. What is committable is the manifest, the
verdicts and the checker's lines, and this is the one thing that moves them
across:

    python tools/import_wave.py --source <dir> --name <wave-name> [--expect-cubes N] [--dry-run]

It reads the whole source and checks it **before writing a byte**, because a
half-imported wave is worse than none: `evidence/waves/<name>/` would then hold
a manifest saying 16384 and verdicts for 9000, and the claim it invites is the
claim the gate exists to refuse. What it demands is exactly what W1 to W4
demand, one step earlier - the manifest is `cube-wave.v2` with the right keys
and types, `split_vars` is a usable split with `n_cubes == 2**s`,
`cubes_sha256` is re-derived from the split rather than believed,
`cube_construction` is the one this repository implements, every cube id
`0..n_cubes-1` has exactly one verdict with `rc == 20` over its own literals,
and the transcripts, if there are any, cover every cube with `ok` true, the
checker's `s VERIFIED`, and a `drat_sha256` that **is a digest** and equals that
cube's verdict, byte count with it. Anything else is refused with the cube
numbers named, three at a time and then a count.

**A verdict is `{cube, lits, rc, wall_s}`; the two proof keys are optional and
may be missing from some cubes and not others.** Demanding all six is what
refused the live `k=17` confirmation wave, which is honest and simply kept no
proofs, so both readers take four keys and treat an absent digest exactly as
they treat a null one. What stops that from being a hole is the **tier rule**,
which is the same one the gate applies: `wave-drat-verified` needs a transcript
line for *every* cube of the wave, each with a real 64-hex digest equal to that
cube's verdict digest. A wave with no transcripts imports and stands at
`unsat-wave`; a wave whose transcripts cover only some cubes is **refused**,
naming how many cubes have no line, because silently importing it would hand
back a wave the operator believes was checked in full when it was not. And the
summary says which tier the source supports and why, so that a confirmation
wave - which looks exactly like a certified one in a directory listing - cannot
be mistaken for one without running the gate to find out.

That the hash has to be a digest and not merely agree is the one place the two
readers had drifted apart, and it cost a wave. A verdict-only campaign records
`drat_sha256: null`, a checker wrapper run over it copies that null into every
transcript line, and equality is then perfectly satisfied by `null == null` -
so the wave imported, declared `wave-drat-verified`, and failed W4 (which does
demand a digest) on every cube. It fails closed, so no false claim was ever
certified; but the destination guard refuses a second import, so correcting it
cost a hand deletion. A wave with no proof hash on record is not drat-verified,
it is `unsat-wave`, and it reaches that by being imported with no transcripts
at all.

`--expect-cubes` is the operator saying out loud what they think they cut; a
disagreement with the manifest means one of the two is about a different wave.

Three properties are not conveniences:

* **The source is never written to.** A campaign is usually still running
  against it, and a tool that consolidated verdicts in place would corrupt a
  wave in flight.
* **Nothing is written outside `evidence/waves/<name>/`.** The name has to be a
  plain directory name, so `--name ../elsewhere` is refused before any path is
  built from it.
* **No `.drat` or `.drat.gz` is ever copied.** They are gitignored bulk. What
  crosses is the transcript line about each one.

A source comes in either storage form too - a resumed campaign appends to one
`verdicts.jsonl` rather than writing thousands of files - and that form is read
under the same rules: a duplicated cube, a line that will not parse and a blank
line are each a refusal, never a skip.

Output is deterministic: the verdicts are written sorted by cube with sorted
keys **whatever order the source holds them in**, ASCII and LF, so a source
appended in the order cubes finished imports to the same bytes as one written in
cube order, and two imports of either are byte-identical. The
manifest is copied byte for byte (and refused if it holds a CR, since every
artifact here is LF). The checker's transcript schema is *not* the gate's -
it records `{cube, ok, tool, tool_rc, verdict, drat_sha256, drat_bytes,
cnf_sha256, check_wall_s}`, which is what a checker knows - so it is normalised
into `{cube, drat_sha256, drat_bytes, proof_path_rel, checker, verdict}`. Every
field of that comes from the source except `proof_path_rel`, which is where
this wave's proof for that cube belongs, `evidence/waves/<name>/proofs/`. That
asserts no file is present, and none is: W4 checks transcripts rather than
proofs precisely so a wave's proofs can be deleted, and `--reverify-drat`
reports the absent ones by count.

Finally it prints the campaign totals - cubes, total and longest solver wall
time, proof bytes, how many cubes kept no proof at all, the tier that buys and
why, encoder, base sha256 - and the claim JSON to paste into
`claims/CLAIMS.json`, twice: as an `upper_bound_wave` standing on this wave
alone, and as the `exact` claim it becomes once a second encoder's wave
confirms it, with the confirming wave's name left as a placeholder and the two
fields no tool can write - `prior_art`, and what actually ran - marked
`REPLACE:`. The declared `evidence_level` is the tier the gate will grant:
`unsat-wave` bare, `wave-drat-verified` with transcripts, and one tier up each
with confirmation. What it does **not** do is regenerate the base instance -
that is W1's job, it costs a full encode, and the gate does it on every run.
Import a wave, then run the gate; nothing is evidence until that exits 0.

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
| Wave generator drops or duplicates a cube | nobody, until it is public | W2 re-derives the cube set from the split; W3 demands every id | re-cut the wave; the claim reddens meanwhile |
| A cube times out and is written `rc: null` | operator | W3 - only rc 20 counts | re-run that cube; nothing is claimed until it lands |
| A wave's proofs are deleted to save disk | operator | W4 checks transcripts, not proofs; `--reverify-drat` reports how many were absent | nothing to undo - transcripts are the record, proofs regenerate |
| One encoder is wrong and every cube of the wave inherits it | nobody | W6: no exact claim without a second encoder | drop to `upper_bound_wave` until a confirming wave exists |
| A claim block is pasted from the previous encoder's wave and keeps its `verdicts_dir` | nobody - the verdicts are genuine, and they decided another instance | W1's wave-directory rule: verdicts, transcripts and proofs are read only from inside the directory the manifest names | correct the claim block; the gate reddens meanwhile |
| Verdict files are copied from one wave's directory into another's | **nobody, and not the gate either** - a `cube-wave.v2` verdict names no instance | not detected; recorded above, and closed only by a v3 verdict carrying the base sha256 and encoder | re-cut the confirming wave |
| A consolidated `verdicts.jsonl` is truncated mid-append by a dying machine | nobody - the file still parses up to the break | W3: the last line is not a verdict and is a failure, not a skip; and the cubes after it have no verdict | re-import the wave from the source, which was never modified |
| A wave is imported while it is still running | nobody, and the claim would say every cube came back UNSAT | `import_wave` reads every cube before writing anything and refuses, naming the missing ones | nothing to undo - it wrote nothing |
| A verdict-only wave (no proof hashes) is run through a checker wrapper and imported as drat-verified | the gate, one step later than it should - W4 wants a digest, and `null == null` had satisfied the import | `import_wave` demands a digest where W4 demands one, so the wave is refused rather than written | it wrote nothing; before this it wrote a wave that had to be deleted by hand, since a second import is refused |
| A wave is solved with `--no-proof`, so no verdict carries a digest, and a resumed one carries them for only some cubes | the operator, when a complete and honest wave is refused | it was not caught by design: run against `scratch/wave274` both readers demanded six keys and refused 75 of 117 verdicts | the pair is optional per cube in the gate and the import alike; such a wave imports and verifies at `unsat-wave` |
| A checker is run over a mixed wave and leaves lines only for the cubes that kept a proof | nobody - a short transcripts file looks like a whole one, and the wave "was checked" | import refuses partial coverage and says how many cubes have no line; W4 fails the claim rather than downgrading it | check the remaining cubes, or import with no transcripts and stand at `unsat-wave` |
| An import is pointed at a name that is a path (`--name ../x`) | nobody | the name must be a plain directory name, checked before any path is built | nothing to undo |

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

**Waves** - the fixture is a real wave, not a mock: `tests/_wavefix.py` cuts the
genuinely UNSAT `k=3, l=2, N=9` instance on two variables and **refutes all four
cubes with `_minisolve` before writing a single verdict**, so `rc: 20` is never
written for a cube this repository has not just decided. What stays synthetic is
provenance - no solver ran, so the wall clock is a constant, and no checker ran,
so the transcript's `checker` field says so in words and the proof bytes are a
placeholder the gate never opens. One bad fixture per rule, each observed
failing: a base sha256 that does not regenerate (W1), a cube-set hash the split
does not produce (W2), a missing cube, an `rc: null` cube, a cube whose literals
are not its own (W3), a transcript whose proof hash is not the verdict's (W4), a
claim whose value is not the manifest's `N` (W5), and an `exact` claim with no
confirmation - repeated with a deliberately understated `evidence_level`, since
the rule is about the assertion and not about its volume (W6). Then the
wave-directory rule, each case built from two waves that are both genuine so
that the only wrong thing is the wiring: a confirming wave stripped of its
verdicts and pointed at the primary's (the tester's exploit, which bought an
`exact` claim at `drat-transcript` off one encoder's solving), a claim taking
another wave's verdicts, its transcripts, or one of its proofs, and a manifest
that is not the `manifest.json` of its own directory. Positive controls
beside them: a confirmed wave, a wave confirmed by a monolithic run-log instead,
and an `upper_bound_wave` standing alone on one encoder. The proofless shapes
get their own set, **in both storage forms because one reader serves both**: a
wave whose verdicts carry no proof keys at all and a mixed wave carrying them
for half its cubes each verify at `unsat-wave`, the same wave declaring
`wave-drat-verified` fails G7, and a checker's line with no digest over a
verdict with no digest fails W4 rather than agreeing with it - two absences
compare equal, which is exactly why W4 demands a digest and not agreement. Each of the four
level-earning shapes is asserted to reach its tier exactly, and to fail G7 one
tier higher. `write_cube_cnf` is checked byte for byte against `write_cnf` fed
the same clauses. `--reverify-drat` over a wave is driven by a **stub checker**
the test writes - a one-line script printing `s VERIFIED` or `s NOT VERIFIED` -
because drat-trim is application-control blocked here and absent from CI, so
otherwise that whole path would run nowhere: the stub proves the gate
decompresses each proof, hashes it against the transcript, rebuilds the cube
instance and believes the answer, none of which needs a real checker to test.

The **consolidated form** is the same fixture written the other way, and each
W3 case is repeated against it: a missing cube, an `rc: null` cube, tampered
literals, a duplicate line, a cube id past the end, a line that is not a
verdict, and a `verdicts.jsonl` borrowed from the other wave. Two of those only
say anything because of how they are built - the duplicate and the junk line
are *extra* lines beside a complete and correct set, so a reader that skipped
them would pass, where corrupting an existing line would be caught by the
missing-cube rule and prove nothing about skipping. The four level-earning
shapes are asserted again in this form: the storage form must not move a claim
up or down the ladder.

**Importing** - `tests/_wavefix.py` also writes the off-repo shape a campaign
leaves behind (`verdicts/vNNNNN.json`, `drat/cube_NNNNN.drat.gz`, and the
checker's own transcript schema), every cube of it refuted by `_minisolve`
first. The round trip is the whole point: import that source, paste the claim
fragment the tool printed, and the gate must exit 0 at the level the tool
declared - so the fragment is checked by the thing it is a fragment for, not by
a string comparison. Beside it, one test per refusal (incomplete, `rc: null`,
satisfiable cube, tampered lits, bad cube hash, wrong `n_cubes`,
`--expect-cubes` disagreeing, transcript short a cube, `ok: false`, transcript
about another proof, a transcript recording no digest at all and one recording a
digest the verdict never had, duplicate transcript line, duplicate verdict,
`cube-wave.v1` manifest, unknown construction, unparseable verdict, source that
is not a wave, name that is a path, destination already occupied), each asserted
to write nothing. The `--no-proof` shapes are their own round trips: a source
whose verdicts carry no proof keys, and a mixed one carrying them for two cubes
of four, each import at `unsat-wave` and the gate must exit 0 on the fragment
the tool printed; transcripts over that mixed wave covering only the two cubes
that kept a proof are refused with a message naming how many cubes have no
line; transcripts over a wave that kept no digests are refused whether the keys
are absent or present and null; and the summary's tier line is read back and
asserted to name the level and the reason, since a tier nobody prints is a tier
the operator finds out about from the gate. Then four properties: the source is byte-identical afterwards,
no `.drat.gz` is anywhere in the repository, `--dry-run` writes nothing at all,
and two imports of one source produce identical bytes.

The **consolidated source** - a resumed campaign's own `verdicts.jsonl` - is
tested as its own round trip, with the duplicate-line, unparseable-line and
blank-line refusals repeated against it, and it is what pins the tool's
determinism: fed cube order back to front with keys in the writer's order, the
import must still write `0,1,2,3` with sorted keys. Fed the fixture's default
layout that assertion cannot fail - `verdicts/v00000.json ..` reads back in cube
order however it is read, and a fixture that sorts its own keys hands them back
sorted - so for a while it did not, and both halves of the tool's ordering could
be deleted with the suite and the gate still green. A test whose fixture
supplies the property under test asserts nothing.

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
| M28 | gate skips the base-instance regeneration behind a wave | wrong-base-sha wave fixture |
| M29 | gate trusts `cubes_sha256` instead of re-deriving the cube set | tampered-cube-hash fixture |
| M30 | gate lets a cube with no verdict pass | missing-cube fixture |
| M31 | gate takes any return code as UNSAT | `rc: null` and `rc: 10` cube fixtures |
| M32 | gate does not check a verdict's literals against the construction | tampered-lits fixture |
| M33 | gate does not tie a transcript line to its cube's proof hash | transcript-sha-mismatch fixture |
| M34 | gate does not check the manifest is about the claimed instance | wrong-`N` and wrong-`k` wave fixtures |
| M35 | gate lets an exact claim rest on a single encoder's wave | exact-without-confirm fixture, at any declared level |
| M36 | `--reverify-drat` does not hash the decompressed proof | substituted-`.drat.gz` fixture, against a stub checker |
| M37 | `--reverify-drat` ignores what the checker said | stub checker printing `s NOT VERIFIED` |
| M38 | gate resolves a wave's `verdicts_dir` anywhere under `evidence/waves/` instead of inside the directory its manifest names | the borrowed-verdicts fixtures, primary and confirming |
| M39 | gate reads a wave's transcripts, or a proof they name, from another wave's directory | the borrowed-transcript and borrowed-proof fixtures |
| M40 | gate takes any `*.json` under `evidence/waves/<name>/` as a wave manifest | the manifest-not-named-`manifest.json` fixture |
| M41 | consolidated reader skips a line it cannot parse instead of failing | the extra-junk-line fixtures (truncated, blank, whitespace) |
| M42 | verdict reader accepts a cube id twice | the duplicate fixtures, **both forms** - one reader serves both |
| M43 | verdict reader accepts a cube id at or beyond `n_cubes` | the extra-id-line fixture |
| M44 | gate resolves a consolidated `verdicts.jsonl` anywhere under `evidence/waves/` | the borrowed-`verdicts.jsonl` fixture |
| M45 | gate reads the storage form off the disk instead of off the recorded name | the directory-named-`verdicts.jsonl` fixture |
| M46 | import does not notice a cube with no verdict | the incomplete-source test |
| M47 | import takes `rc: null` as decided | the timed-out-cube source |
| M48 | import does not check a verdict's literals | the tampered-lits source |
| M49 | import trusts `cubes_sha256` instead of re-deriving it from the split | the tampered-cube-hash source |
| M50 | import does not require a transcript line per cube | the short-transcripts source |
| M51 | import ignores a transcript's `ok` | the `ok: false` source |
| M52 | import does not tie a transcript line to its cube's proof hash | the wrong-sha transcript source |
| M53 | import accepts two verdicts for one cube | the resumed-wave source, which holds one twice |
| M54 | import overwrites a wave already on record | the second-import test |
| M55 | import accepts a wave name that is a path | the `--name ../elsewhere` cases |
| M56 | import checks a transcript's hash for equality only, not that it is a digest | the proofless-wave-with-transcripts source, whose nulls agree |
| M57 | import writes the verdicts in the order it read them (`list(verdicts.values())`) | the resumed source, appended back to front |
| M58 | import writes the verdicts with unsorted keys (`json.dumps` without `sort_keys`) | the resumed source, whose own keys are not sorted |
| M59 | import's consolidated reader accepts a cube id twice | the duplicate-line consolidated source |
| M60 | import's consolidated reader skips a line it cannot read instead of failing | the truncated, blank and whitespace consolidated sources |
| M61 | verdict reader demands the two proof keys (`set(document) != VERDICT_KEYS`) | the no-proof-keys and mixed wave fixtures, **both storage forms** - one reader serves both |
| M62 | import demands the two proof keys | the no-proof-keys and mixed sources, which are the shape the live campaign writes |
| M63 | import drops the cubes a transcripts file does not cover instead of refusing | the part-covered mixed source, which would otherwise import declaring `wave-drat-verified` |
| M64 | verdict reader treats a missing required key as an absent proof key | the `wall_s`-removed line fixture |
| M65 | import's summary names a tier from whether a transcripts file exists rather than from what it covers, or prints none at all | the tier-line tests over a checked and a proofless source |

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
