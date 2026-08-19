"""The gate. The exit code of this script is the only claim this repository makes.

It runs with no SAT solver installed, trusts no cached verdict and reads no
stored "verified" flag. Every claim is re-derived from artifacts on disk:
witnesses are re-evaluated here, and each claimed UNSAT instance is regenerated
from its recorded parameters and compared byte for byte by sha256. A cached
verdict is a promise; regeneration is evidence.

    python gate/verify_all.py
    python gate/verify_all.py --root tests/fixtures/bad_witness_sign

Rules G1 to G7 and W1 to W6 are documented in docs/TDD.md. Failures print
``FAIL <rule> <claim-id> <reason>`` and set a non-zero exit code. WARN and INFO
lines never change the exit code: an unreferenced artifact is untidiness, and a
claim that understates its evidence is not an error.

The W rules are the same idea applied to a cube-and-conquer wave, where one
UNSAT run becomes thousands and the load-bearing question becomes whether the
cubes that ran are *every* case. The gate never reads a cube file: it re-derives
the cube set from the split recorded in the manifest and hashes that.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple

if __package__ in (None, ""):  # allow `python gate/verify_all.py` from a clean checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nk2 import encode_seqcount, encode_subsets, encode_totalizer  # noqa: E402
from nk2.cubes import (  # noqa: E402
    CONSTRUCTION,
    CubeError,
    check_split,
    cube_literals,
    cubes_sha256,
    write_cube_cnf,
)
from nk2.dimacs import write_cnf  # noqa: E402
from nk2.evaluator import avoids  # noqa: E402
from nk2.witness import WitnessFormatError, read_witness  # noqa: E402

# G5 holds its own copy of the published terms so that a corrupted ANCHORS.json
# cannot quietly redefine what "consistent with the literature" means. The two
# copies are compared, and a test compares the even-k entries against the
# Spencer parity formula.
ANCHOR_TERMS = (3, 9, 13, 22, 11, 49, 57, 65, 19, 112, 45, 158, 27, 225, 241)
ANCHOR_OFFSET = 2
ANCHOR_SEQUENCE = "A398541"
LAST_KNOWN_K = ANCHOR_OFFSET + len(ANCHOR_TERMS) - 1  # 16

ENCODERS = {
    encode_subsets.NAME: encode_subsets,
    encode_seqcount.NAME: encode_seqcount,
    encode_totalizer.NAME: encode_totalizer,
}

KINDS = ("lower_bound", "upper_bound", "upper_bound_wave", "exact")

# Ordered weakest to strongest. Where two kinds of evidence are genuinely
# incomparable the weaker floor sorts lower, because the order exists to refuse
# overstatement rather than to rank achievements:
#
#   witness             a coloring this gate re-evaluated itself
#   unsat-wave          a complete cube decomposition, every cube rc 20, one
#                       encoder; the solver's word is taken for each cube
#   wave-drat-verified  the same, plus a checker's recorded verdict on every
#                       cube's proof: the solver is no longer trusted, but one
#                       encoding still decides everything
#   unsat-dual          two structurally different encodings agree, each
#                       instance regenerated here; the encoder risk is covered,
#                       the solver's word is taken
#   drat-transcript     both: two encodings, and a checker's recorded verdict
#                       (a monolithic proof under G4, or a confirmed wave whose
#                       every cube is proof-checked)
#   drat-reverified     the gate re-ran the checker itself
#
# wave-drat-verified sits below unsat-dual because a DRAT proof says nothing
# about whether the CNF encodes the problem: 16384 checked proofs of a wrong
# encoding are 16384 checked proofs of the wrong thing. Encoder diversity is
# what this repository's soundness argument rests on, so a level that lacks it
# may not outrank one that has it.
LEVELS = (
    "witness",
    "unsat-wave",
    "wave-drat-verified",
    "unsat-dual",
    "drat-transcript",
    "drat-reverified",
)
LEVEL_WITNESS = LEVELS.index("witness") + 1
LEVEL_UNSAT_WAVE = LEVELS.index("unsat-wave") + 1
LEVEL_WAVE_DRAT = LEVELS.index("wave-drat-verified") + 1
LEVEL_UNSAT_DUAL = LEVELS.index("unsat-dual") + 1
LEVEL_DRAT_TRANSCRIPT = LEVELS.index("drat-transcript") + 1
LEVEL_DRAT_REVERIFIED = LEVELS.index("drat-reverified") + 1

# `wave` is the one optional key. Every claim written before cube waves existed
# omits it, and absent has to keep meaning null or the committed record stops
# verifying; absent can only ever lower a claim's evidence, never raise it, so
# the default is safe. Everything else is required, and any key not listed here
# is refused.
CLAIM_KEYS = {
    "id", "k", "l", "kind", "value", "witness", "unsat_runs", "drat", "wave",
    "evidence_level", "prior_art", "notes",
}
REQUIRED_CLAIM_KEYS = CLAIM_KEYS - {"wave"}
WITNESS_KEYS = {"path", "sha256"}
DRAT_KEYS = {"proof_sha256", "proof_bytes", "transcript"}
WAVE_KEYS = {"manifest", "verdicts_dir", "transcripts", "confirm"}
CONFIRM_WAVE_KEYS = {"kind", "manifest", "verdicts_dir", "transcripts"}
CONFIRM_RUNS_KEYS = {"kind"}

MANIFEST_SCHEMA = "cube-wave.v2"
MANIFEST_KEYS = {
    "schema", "N", "k", "l", "encoder", "symmetry_break", "snapshot_commit",
    "base", "split_vars", "n_cubes", "cubes_sha256", "cube_construction",
}
MANIFEST_BASE_KEYS = {"n_vars", "n_clauses", "sha256"}
# A verdict always says which cube it is about, on which literals, what the
# solver returned and how long it took. The two proof keys are optional per
# cube: a wave solved with the driver's --no-proof mode - which is how a
# second-encoder confirmation wave is run - kept no proof, so it has no digest
# to record and leaves the pair out. That is the honest shape, and a wave
# interrupted and resumed in the other mode carries both. Absent means "no
# proof hash on record", which can only ever lower what a wave is worth: W4
# demands a digest, so a cube without one cannot be part of a drat-verified
# wave.
VERDICT_REQUIRED_KEYS = {"cube", "lits", "rc", "wall_s"}
VERDICT_PROOF_KEYS = {"drat_sha256", "drat_bytes"}
VERDICT_KEYS = VERDICT_REQUIRED_KEYS | VERDICT_PROOF_KEYS
TRANSCRIPT_KEYS = {
    "cube", "drat_sha256", "drat_bytes", "proof_path_rel", "checker", "verdict",
}
PROOF_SUFFIX = ".drat.gz"
# A wave's verdicts are committed either as a directory of one JSON file per
# cube or, once there are thousands of them, as one file holding the same
# objects a line each. The recorded path's own name says which: sixteen
# thousand files is not a repository anybody can clone or read on GitHub.
VERDICTS_SUFFIX = ".jsonl"
# A wave can hold tens of thousands of cubes and a single mistake in the
# generator breaks all of them at once. Print enough to diagnose, then a count.
EXAMPLES = 3

# A single drive letter, not the tail of a URL scheme: `https:/` must not trip
# this, and neither must the rule's own definition in docs/TDD.md, which quotes
# these patterns as text. Requiring a following path character keeps the rule
# self-consistent while still catching any real machine path.
#
# The doubled separator is not cosmetic: run-logs are JSON, and json.dumps
# escapes every backslash in a leaked Windows path, so a pattern that matched
# only a single separator would walk straight past the likeliest case of all.
# Fixtures covering both spellings live in tests/fixtures/g6_*.
ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]+[A-Za-z0-9_.$-]"),
    re.compile(r"/home/[A-Za-z0-9_.-]"),
    re.compile(r"/Users/[A-Za-z0-9_.-]"),
)

# Where each kind of artifact has to live, from the data model in docs/TDD.md.
# `root / <string out of CLAIMS.json>` is not a containment check on its own: it
# resolves `../elsewhere/witness.txt` happily, it takes an absolute path by
# replacing the root outright, and it accepts a path into the gitignored
# scratch/ or evidence/drat/ trees. Any of those makes the gate say "verified
# from artifacts on disk" for a checkout that does not contain the artifact -
# green here, red for a stranger - which is the deception the gate exists to
# prevent. So a claimed path must be a plain repo-relative path to a committed
# artifact, in the directory that kind of artifact belongs in.
WITNESS_DIR = "evidence/witnesses"
RUNS_DIR = "evidence/runs"
TRANSCRIPTS_DIR = "evidence/transcripts"
WAVES_DIR = "evidence/waves"
EVIDENCE_DIR = "evidence"
# A wave is one directory, `evidence/waves/<name>/`, and its manifest is the
# file that names it. Everything else the wave is read from - verdicts,
# transcripts, proofs - has to sit inside that directory. See wave_directory.
WAVE_MANIFEST_NAME = "manifest.json"
WAVE_DIR_DEPTH = len(PurePosixPath(WAVES_DIR).parts) + 1
# Gitignored wholesale, so a committed artifact never carries one of these.
# Instances and proofs do, which is why they are checked for containment only.
# `.gz` is here because a wave's proofs are committed nowhere and stored
# compressed: `cube00000.drat.gz` has suffix `.gz`, not `.drat`.
BULK_SUFFIXES = {".cnf", ".drat", ".gz"}

SKIP_DIR_NAMES = {
    ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "node_modules",
}
# Relative to --root. Fixtures deliberately contain the shapes G6 rejects, so
# they are excluded here and scanned when a fixture is itself the root.
SKIP_RELATIVE = {Path("evidence/drat"), Path("tests/fixtures"), Path("scratch")}
# `.gz` holds compressed proofs: opaque bytes, so the ASCII scan would fail on
# the first one and every wave with its proofs still on disk would redden G6.
SKIP_SUFFIXES = {".cnf", ".drat", ".gz", ".tmp", ".pyc"}


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self._claim_failures: dict[str, int] = {}

    def fail(self, rule: str, claim_id: str, reason: str) -> None:
        self.failures += 1
        self._claim_failures[claim_id] = self._claim_failures.get(claim_id, 0) + 1
        print(f"FAIL {rule} {claim_id} {reason}")

    def warn(self, reason: str) -> None:
        print(f"WARN {reason}")

    def info(self, reason: str) -> None:
        print(f"INFO {reason}")

    def clean(self, claim_id: str) -> bool:
        return self._claim_failures.get(claim_id, 0) == 0


class ArtifactPathError(ValueError):
    """A recorded path does not name an artifact of the repository being checked."""


def artifact_path(root: Path, raw: object, label: str, under: str, committed: bool = True) -> Path:
    """Resolve one recorded path, or refuse it and say why.

    ``under`` is the directory the artifact has to sit in; ``committed`` is
    False for the bulk files a transcript names, which are gitignored by design
    and only have to stay inside the repository.
    """
    if not isinstance(raw, str):
        raise ArtifactPathError(f"{label} path {raw!r} is not a string")
    if not raw.strip():
        raise ArtifactPathError(f"{label} path {raw!r} is empty")
    if "\\" in raw or raw.startswith(("/", "~")) or PureWindowsPath(raw).drive:
        raise ArtifactPathError(f"{label} path {raw!r} is not a plain repo-relative path")
    relative = PurePosixPath(raw)
    if ".." in relative.parts:
        raise ArtifactPathError(f"{label} path {raw!r} climbs out of the repository")
    # `./evidence/x` and `evidence//x` name the right file and are not the
    # string the artifact is filed under, which would leave every claim's own
    # cross-reference - warn_unreferenced, and any reader - comparing unequal
    # spellings of one path. One spelling is the rule.
    if relative.as_posix() != raw:
        raise ArtifactPathError(f"{label} path {raw!r} is not a plain repo-relative path")
    if not relative.is_relative_to(under) or relative == PurePosixPath(under):
        raise ArtifactPathError(f"{label} path {raw!r} is outside {under}/")
    if committed and relative.suffix.lower() in BULK_SUFFIXES:
        raise ArtifactPathError(f"{label} path {raw!r} names a gitignored bulk artifact")
    resolved = root / relative
    # A symlink passes every check above and still points anywhere on the disk.
    if not resolved.resolve().is_relative_to(root.resolve()):
        raise ArtifactPathError(f"{label} path {raw!r} resolves outside the repository")
    return resolved


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> object:
    return json.loads(path.read_bytes().decode("ascii"))


def regenerate(N: int, k: int, l: int, encoder: str, symmetry_break: bool) -> tuple[str, int, int]:
    """Rebuild an instance in a temp directory and return ``(sha256, vars, clauses)``."""
    module = ENCODERS[encoder]
    n_vars, clauses = module.build(N, k, l, symmetry_break=symmetry_break)
    with tempfile.TemporaryDirectory(prefix="nk2gate-") as work:
        info = write_cnf(Path(work) / "regenerated.cnf", n_vars, clauses)
        return str(info["sha256"]), int(info["n_vars"]), int(info["n_clauses"])


# --- G1 ---------------------------------------------------------------------


def rule_g1(claim: object, index: int, report: Report) -> str | None:
    """Schema. Returns the claim id if it is usable, else None."""
    if not isinstance(claim, dict):
        report.fail("G1", f"#{index}", "claim is not an object")
        return None
    raw_id = claim.get("id")
    claim_id = raw_id if isinstance(raw_id, str) and raw_id else f"#{index}"

    unknown = set(claim) - CLAIM_KEYS
    missing = REQUIRED_CLAIM_KEYS - set(claim)
    if unknown:
        report.fail("G1", claim_id, f"unknown key(s) {sorted(unknown)}")
    if missing:
        report.fail("G1", claim_id, f"missing key(s) {sorted(missing)}")
    if unknown or missing:
        return None

    ok = True

    def bad(reason: str) -> None:
        nonlocal ok
        ok = False
        report.fail("G1", claim_id, reason)

    if not isinstance(claim["id"], str) or not claim["id"]:
        bad("id must be a non-empty string")
    if claim["kind"] not in KINDS:
        bad(f"unknown kind {claim['kind']!r}; expected one of {list(KINDS)}")
    for field in ("k", "l", "value"):
        value = claim[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            bad(f"{field} must be a positive integer, got {value!r}")
    if isinstance(claim["k"], int) and not isinstance(claim["k"], bool) and claim["k"] < 2:
        bad("k must be at least 2")
    if claim["evidence_level"] not in LEVELS:
        bad(f"unknown evidence_level {claim['evidence_level']!r}; expected {list(LEVELS)}")

    witness = claim["witness"]
    if witness is not None:
        if not isinstance(witness, dict) or set(witness) != WITNESS_KEYS:
            bad(f"witness must be null or {sorted(WITNESS_KEYS)}")
        elif not all(isinstance(witness[key], str) for key in WITNESS_KEYS):
            bad("witness path and sha256 must be strings")

    runs = claim["unsat_runs"]
    if not isinstance(runs, list) or not all(isinstance(run, str) for run in runs):
        bad("unsat_runs must be a list of paths")

    drat = claim["drat"]
    if drat is not None:
        if not isinstance(drat, dict) or set(drat) != DRAT_KEYS:
            bad(f"drat must be null or {sorted(DRAT_KEYS)}")
        elif not isinstance(drat["proof_bytes"], int) or isinstance(drat["proof_bytes"], bool):
            bad("drat.proof_bytes must be an integer")

    wave = claim.get("wave")
    if wave is not None:
        if not isinstance(wave, dict) or set(wave) != WAVE_KEYS:
            bad(f"wave must be null or {sorted(WAVE_KEYS)}")
        else:
            if not all(isinstance(wave[key], str) for key in ("manifest", "verdicts_dir")):
                bad("wave.manifest and wave.verdicts_dir must be strings")
            if wave["transcripts"] is not None and not isinstance(wave["transcripts"], str):
                bad("wave.transcripts must be null or a string")
            confirm = wave["confirm"]
            if confirm is not None:
                if not isinstance(confirm, dict) or confirm.get("kind") not in (
                    "wave", "unsat_runs"
                ):
                    bad("wave.confirm must be null or an object with kind 'wave' or 'unsat_runs'")
                elif confirm["kind"] == "wave":
                    if set(confirm) != CONFIRM_WAVE_KEYS:
                        bad(f"a confirm of kind 'wave' takes exactly {sorted(CONFIRM_WAVE_KEYS)}")
                    elif not all(
                        isinstance(confirm[key], str) for key in ("manifest", "verdicts_dir")
                    ):
                        bad("confirm.manifest and confirm.verdicts_dir must be strings")
                    elif confirm["transcripts"] is not None and not isinstance(
                        confirm["transcripts"], str
                    ):
                        bad("confirm.transcripts must be null or a string")
                elif set(confirm) != CONFIRM_RUNS_KEYS:
                    bad(
                        "a confirm of kind 'unsat_runs' takes no other key; it points at the "
                        "claim's own unsat_runs"
                    )

    for field in ("prior_art", "notes"):
        if not isinstance(claim[field], str):
            bad(f"{field} must be a string")

    return claim_id if ok else None


# --- G2 ---------------------------------------------------------------------


def rule_g2(claim: dict, claim_id: str, root: Path, report: Report) -> bool:
    """Lower bound V: a witness of length V-1 that this gate itself re-evaluates."""
    needed = claim["kind"] in ("lower_bound", "exact")
    witness = claim["witness"]
    if witness is None:
        if needed:
            report.fail("G2", claim_id, f"{claim['kind']} needs a witness, found null")
        return False

    try:
        path = artifact_path(root, witness["path"], "witness", WITNESS_DIR)
    except ArtifactPathError as exc:
        report.fail("G2", claim_id, str(exc))
        return False
    if not path.is_file():
        report.fail("G2", claim_id, f"witness {witness['path']} does not exist")
        return False
    actual = sha256_bytes(path.read_bytes())
    if actual != witness["sha256"]:
        report.fail(
            "G2", claim_id,
            f"witness sha256 mismatch: recorded {witness['sha256'][:16]}, on disk {actual[:16]}",
        )
        return False
    try:
        coloring, _ = read_witness(path)
    except (WitnessFormatError, OSError) as exc:
        report.fail("G2", claim_id, f"witness will not parse: {exc}")
        return False

    if needed and len(coloring) != claim["value"] - 1:
        report.fail(
            "G2", claim_id,
            f"witness has length {len(coloring)}, a bound of {claim['value']} "
            f"needs {claim['value'] - 1}",
        )
        return False
    # Re-run the arithmetic here. No stored verified-flag is ever read.
    if not avoids(coloring, claim["k"], claim["l"]):
        report.fail(
            "G2", claim_id,
            f"witness does not avoid (k={claim['k']}, l={claim['l']})",
        )
        return False
    return True


# --- G3 ---------------------------------------------------------------------


def rule_g3(
    claim: dict, claim_id: str, root: Path, report: Report
) -> tuple[bool, set[str], set[str]]:
    """Upper bound V: two distinct encoders, rc 20, and instances that regenerate.

    Returns ``(two encoders agreed, instance sha256s, encoders behind valid
    run-logs)``. The last of those is what W6 reads when a wave nominates the
    claim's monolithic run-logs as its second encoding.
    """
    # An upper bound may rest on a cube wave instead of on monolithic runs; the
    # W rules take over there, and unsat_runs is then optional corroboration.
    # `upper_bound_wave` never needs run-logs at all.
    on_a_wave = claim.get("wave") is not None
    needed = claim["kind"] == "upper_bound" or (claim["kind"] == "exact" and not on_a_wave)
    runs = claim["unsat_runs"]
    shas: set[str] = set()
    encoders: set[str] = set()
    if not runs:
        if needed:
            report.fail("G3", claim_id, f"{claim['kind']} needs UNSAT run-logs, found none")
        return False, shas, encoders

    ok = True
    for run_rel in runs:
        try:
            path = artifact_path(root, run_rel, "run-log", RUNS_DIR)
        except ArtifactPathError as exc:
            report.fail("G3", claim_id, str(exc))
            ok = False
            continue
        if not path.is_file():
            report.fail("G3", claim_id, f"run-log {run_rel} does not exist")
            ok = False
            continue
        try:
            log = load_json(path)
        except (ValueError, UnicodeDecodeError) as exc:
            report.fail("G3", claim_id, f"run-log {run_rel} will not parse: {exc}")
            ok = False
            continue
        if not isinstance(log, dict) or log.get("schema") != "nk2.runlog.v1":
            report.fail("G3", claim_id, f"run-log {run_rel} is not an nk2.runlog.v1 object")
            ok = False
            continue

        instance = log.get("instance")
        if not isinstance(instance, dict):
            report.fail("G3", claim_id, f"run-log {run_rel} has no instance block")
            ok = False
            continue

        # The verdict alone is not enough: the raw return code has to say 20.
        if log.get("verdict") != "UNSAT" or log.get("rc") != 20:
            report.fail(
                "G3", claim_id,
                f"run-log {run_rel} is verdict {log.get('verdict')!r} with rc {log.get('rc')!r}; "
                "an UNSAT claim needs verdict UNSAT and rc 20",
            )
            ok = False
            continue

        want = (claim["value"], claim["k"], claim["l"])
        got = (instance.get("N"), instance.get("k"), instance.get("l"))
        if got != want:
            report.fail(
                "G3", claim_id,
                f"run-log {run_rel} is (N,k,l)={got}, the claim needs {want}",
            )
            ok = False
            continue

        encoder = instance.get("encoder")
        if encoder not in ENCODERS:
            report.fail("G3", claim_id, f"run-log {run_rel} names unknown encoder {encoder!r}")
            ok = False
            continue

        try:
            sha, n_vars, n_clauses = regenerate(
                int(instance["N"]), int(instance["k"]), int(instance["l"]),
                str(encoder), bool(instance.get("symmetry_break", False)),
            )
        except (ValueError, KeyError, TypeError) as exc:
            report.fail("G3", claim_id, f"run-log {run_rel} will not regenerate: {exc}")
            ok = False
            continue

        if sha != instance.get("sha256"):
            report.fail(
                "G3", claim_id,
                f"run-log {run_rel} instance sha256 mismatch: recorded "
                f"{str(instance.get('sha256'))[:16]}, regenerated {sha[:16]}",
            )
            ok = False
            continue
        if (n_vars, n_clauses) != (instance.get("n_vars"), instance.get("n_clauses")):
            report.fail(
                "G3", claim_id,
                f"run-log {run_rel} records {instance.get('n_vars')} vars / "
                f"{instance.get('n_clauses')} clauses, regeneration gives {n_vars} / {n_clauses}",
            )
            ok = False
            continue

        encoders.add(str(encoder))
        shas.add(sha)

    if len(encoders) < 2:
        if needed:
            report.fail(
                "G3", claim_id,
                f"only {len(encoders)} encoder(s) {sorted(encoders)} behind an UNSAT claim; "
                "two structurally different encodings are required",
            )
        return False, shas, encoders
    return ok, shas, encoders


# --- G4 ---------------------------------------------------------------------


def find_drat_trim() -> str | None:
    directory = os.environ.get("NK2_DRAT_TRIM_DIR", "")
    for base in [d for d in directory.split(os.pathsep) if d]:
        for candidate in (Path(base) / "drat-trim", Path(base) / "drat-trim.exe"):
            if candidate.is_file():
                return str(candidate)
    return shutil.which("drat-trim")


def rule_g4(
    claim: dict, claim_id: str, root: Path, instance_shas: set[str],
    reverify: bool, report: Report,
) -> int:
    """DRAT bookkeeping. Returns the highest level this claim's proof supports."""
    drat = claim["drat"]
    if drat is None:
        # Not a failure. It only means drat-transcript is unreachable, and G7
        # catches any claim that says otherwise.
        return 0

    try:
        transcript_path = artifact_path(root, drat["transcript"], "transcript", TRANSCRIPTS_DIR)
    except ArtifactPathError as exc:
        report.fail("G4", claim_id, str(exc))
        return 0
    if not transcript_path.is_file():
        report.fail("G4", claim_id, f"transcript {drat['transcript']} does not exist")
        return 0
    try:
        transcript = load_json(transcript_path)
    except (ValueError, UnicodeDecodeError) as exc:
        report.fail("G4", claim_id, f"transcript will not parse: {exc}")
        return 0
    if not isinstance(transcript, dict):
        report.fail("G4", claim_id, "transcript is not an object")
        return 0

    if transcript.get("proof_sha256") != drat["proof_sha256"]:
        report.fail("G4", claim_id, "transcript proof sha256 does not match the claim")
        return 0
    if transcript.get("proof_bytes") != drat["proof_bytes"]:
        report.fail("G4", claim_id, "transcript proof byte count does not match the claim")
        return 0
    if transcript.get("instance_sha256") not in instance_shas:
        report.fail(
            "G4", claim_id,
            "transcript instance sha256 is not one of the instances verified by G3",
        )
        return 0

    tail = [line for line in transcript.get("output_tail", []) if str(line).strip()]
    if not tail or str(tail[-1]).strip() != "s VERIFIED":
        report.fail("G4", claim_id, f"transcript does not end 's VERIFIED' (ends {tail[-1:]})")
        return 0

    # The proof and the instance are gitignored bulk, so they only have to be
    # inside the evidence tree - but they do have to be inside it. Both are fed
    # to drat-trim below, and a path out of the repository would hand a
    # subprocess a file nobody checking this claim can see.
    proof_rel = transcript.get("proof_path_rel")
    proof_path: Path | None = None
    if proof_rel is not None:
        try:
            proof_path = artifact_path(root, proof_rel, "proof", EVIDENCE_DIR, committed=False)
        except ArtifactPathError as exc:
            report.fail("G4", claim_id, str(exc))
            return 0
        if proof_path.is_file():
            if sha256_bytes(proof_path.read_bytes()) != drat["proof_sha256"]:
                report.fail("G4", claim_id, "proof on disk does not match the recorded sha256")
                return 0
            if proof_path.stat().st_size != drat["proof_bytes"]:
                report.fail("G4", claim_id, "proof on disk does not match the recorded byte count")
                return 0

    instance_rel = transcript.get("instance_path_rel")
    instance_path: Path | None = None
    if instance_rel is not None:
        try:
            instance_path = artifact_path(
                root, instance_rel, "instance", EVIDENCE_DIR, committed=False
            )
        except ArtifactPathError as exc:
            report.fail("G4", claim_id, str(exc))
            return 0

    if not reverify:
        return LEVEL_DRAT_TRANSCRIPT

    binary = find_drat_trim()
    if binary is None:
        report.info(f"{claim_id}: --reverify-drat asked for, no drat-trim binary found")
        return LEVEL_DRAT_TRANSCRIPT
    if proof_path is None or not proof_path.is_file():
        report.info(f"{claim_id}: --reverify-drat asked for, the proof itself is not on disk")
        return LEVEL_DRAT_TRANSCRIPT
    if instance_path is None:
        report.fail("G4", claim_id, "transcript records no instance path to re-verify against")
        return 0
    completed = subprocess.run(
        [binary, str(instance_path), str(proof_path)],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0 or "s VERIFIED" not in completed.stdout:
        report.fail("G4", claim_id, f"drat-trim re-run did not verify (rc {completed.returncode})")
        return 0
    return LEVEL_DRAT_REVERIFIED


# --- G5 ---------------------------------------------------------------------


def rule_g5_anchors(claims_dir: Path, report: Report) -> None:
    path = claims_dir / "ANCHORS.json"
    if not path.is_file():
        report.fail("G5", "-", f"{path.name} does not exist")
        return
    try:
        anchors = load_json(path)
    except (ValueError, UnicodeDecodeError) as exc:
        report.fail("G5", "-", f"ANCHORS.json will not parse: {exc}")
        return
    if not isinstance(anchors, dict) or set(anchors) != {
        "schema", "sequence", "offset", "terms", "source"
    }:
        report.fail("G5", "-", "ANCHORS.json keys are not exactly the expected set")
        return
    if anchors["sequence"] != ANCHOR_SEQUENCE:
        report.fail(
            "G5", "-", f"ANCHORS.json is for {anchors['sequence']!r}, not {ANCHOR_SEQUENCE}"
        )
    if anchors["offset"] != ANCHOR_OFFSET:
        report.fail("G5", "-", f"ANCHORS.json offset is {anchors['offset']!r}, not {ANCHOR_OFFSET}")
    if list(anchors["terms"]) != list(ANCHOR_TERMS):
        report.fail(
            "G5", "-",
            "ANCHORS.json terms disagree with the copy held in the gate; "
            "correct both or neither",
        )


def rule_g5_claim(claim: dict, claim_id: str, report: Report) -> None:
    k, value, kind = claim["k"], claim["value"], claim["kind"]
    if k <= LAST_KNOWN_K:
        anchor = ANCHOR_TERMS[k - ANCHOR_OFFSET]
        if claim["l"] != 2:
            return  # anchors are N(k,2) only
        if kind == "exact" and value != anchor:
            report.fail("G5", claim_id, f"exact {value} contradicts published a({k}) = {anchor}")
        elif kind == "lower_bound" and value > anchor:
            report.fail(
                "G5", claim_id, f"lower bound {value} exceeds published a({k}) = {anchor}"
            )
        elif kind in ("upper_bound", "upper_bound_wave") and value < anchor:
            report.fail(
                "G5", claim_id, f"upper bound {value} is below published a({k}) = {anchor}"
            )
    elif kind == "exact" and k > LAST_KNOWN_K + 1:
        report.fail(
            "G5", claim_id,
            f"exact a({k}) is not contiguous with a({LAST_KNOWN_K}); "
            f"a({LAST_KNOWN_K + 1}) is still open",
        )


# --- G6 ---------------------------------------------------------------------


def scanned_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [
            d for d in sorted(dirnames)
            if d not in SKIP_DIR_NAMES
            and (here / d).relative_to(root) not in SKIP_RELATIVE
        ]
        for name in sorted(filenames):
            path = here / name
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            yield path


def rule_g6(root: Path, report: Report) -> None:
    for path in scanned_files(root):
        try:
            raw = path.read_bytes()
        except OSError as exc:
            report.fail("G6", path.name, f"unreadable: {exc}")
            continue
        rel = path.relative_to(root).as_posix()
        offending = [i for i, byte in enumerate(raw) if byte >= 0x80]
        if offending:
            report.fail("G6", rel, f"non-ASCII byte at offset {offending[0]}")
            continue
        text = raw.decode("ascii")
        for pattern in ABSOLUTE_PATH_PATTERNS:
            found = pattern.search(text)
            if found:
                report.fail(
                    "G6", rel, f"absolute path {found.group(0)!r} at offset {found.start()}"
                )
                break


# --- G7 ---------------------------------------------------------------------


def rule_g7(claim: dict, claim_id: str, achieved: int, report: Report) -> None:
    declared = LEVELS.index(claim["evidence_level"]) + 1
    if achieved < declared:
        reached = LEVELS[achieved - 1] if achieved else "nothing"
        report.fail(
            "G7", claim_id,
            f"declares evidence_level {claim['evidence_level']!r} but the artifacts reach "
            f"{reached!r}",
        )
    elif achieved > declared:
        report.info(
            f"{claim_id}: understates its evidence - declares "
            f"{claim['evidence_level']!r}, reaches {LEVELS[achieved - 1]!r}"
        )


# --- W1 to W6: cube-and-conquer waves ---------------------------------------
#
# A wave decides one instance by deciding 2**s derived ones, so it is only a
# proof of the original if the cube set really is every case. Nothing here reads
# a cube file: the gate re-derives every cube from the split in the manifest and
# hashes the result, because a cube file is exactly the artifact a wrong wave
# would look right in.


class Verdict(NamedTuple):
    lits: list[int]
    rc: object
    drat_sha256: object
    drat_bytes: object


class WaveCheck(NamedTuple):
    """What one wave block turned out to be worth."""

    ok: bool  # W1 to W3 all passed: a complete decomposition, every cube UNSAT
    manifest: dict | None  # parsed and well-shaped, whether or not W1 passed
    transcripts_verified: bool  # W4 passed over one transcript line per cube


def is_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(c in "0123456789abcdef" for c in value)
    )


def report_examples(report: Report, rule: str, claim_id: str, problems: list[str]) -> None:
    for problem in problems[:EXAMPLES]:
        report.fail(rule, claim_id, problem)
    if len(problems) > EXAMPLES:
        report.fail(rule, claim_id, f"... and {len(problems) - EXAMPLES} more like it")


def wave_directory(
    root: Path, claim_id: str, label: str, manifest_rel: object, report: Report
) -> str | None:
    """W1: the directory whose contents are this wave, named by its manifest.

    A verdict names no instance, no encoder and no N - it is
    ``{cube, lits, rc, wall_s}`` and an optional proof digest, and ``lits`` is a
    function of the split alone - so two waves that share a split write
    byte-identical verdicts. Nothing inside the artifacts binds a body of
    solving to the instance it decided; the only thing that can is where it
    sits. So a wave is exactly one directory ``evidence/waves/<name>/``, its
    manifest is the ``manifest.json`` that names that directory, and its
    verdicts, transcripts and proofs are read from inside it and nowhere else.

    Without this the gate certifies solving that was done for another instance
    or another encoder: point a second encoder's ``verdicts_dir`` at the first
    encoder's verdicts and W6 is satisfied by a single manifest file, which nk2
    writes with no solver in the room. Pinning the file name is what makes one
    directory mean one wave - two manifests in a directory could otherwise
    share its verdicts and pass containment while doing it.
    """
    try:
        path = artifact_path(root, manifest_rel, f"{label} manifest", WAVES_DIR)
    except ArtifactPathError as exc:
        report.fail("W1", claim_id, str(exc))
        return None
    relative = path.relative_to(root).as_posix()
    parts = PurePosixPath(relative).parts
    if len(parts) != WAVE_DIR_DEPTH + 1 or parts[-1] != WAVE_MANIFEST_NAME:
        report.fail(
            "W1", claim_id,
            f"{label} manifest {relative!r} is not {WAVES_DIR}/<name>/{WAVE_MANIFEST_NAME}; a "
            "wave is one directory, and its manifest is the file that names it",
        )
        return None
    return PurePosixPath(relative).parent.as_posix()


def read_manifest(
    root: Path, claim_id: str, label: str, manifest_rel: object, report: Report
) -> dict | None:
    """W1, first half: the manifest exists, parses and has the right shape."""
    try:
        path = artifact_path(root, manifest_rel, f"{label} manifest", WAVES_DIR)
    except ArtifactPathError as exc:
        report.fail("W1", claim_id, str(exc))
        return None
    if not path.is_file():
        report.fail("W1", claim_id, f"{label} manifest {manifest_rel} does not exist")
        return None
    try:
        manifest = load_json(path)
    except (ValueError, UnicodeDecodeError) as exc:
        report.fail("W1", claim_id, f"{label} manifest will not parse: {exc}")
        return None
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        report.fail(
            "W1", claim_id, f"{label} manifest keys are not exactly {sorted(MANIFEST_KEYS)}"
        )
        return None
    if manifest["schema"] != MANIFEST_SCHEMA:
        report.fail(
            "W1", claim_id,
            f"{label} manifest is schema {manifest['schema']!r}, not {MANIFEST_SCHEMA!r}",
        )
        return None

    problems: list[str] = []
    for field in ("N", "k", "l", "n_cubes"):
        value = manifest[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            problems.append(f"{label} {field} must be a positive integer, got {value!r}")
    if manifest["encoder"] not in ENCODERS:
        problems.append(f"{label} names unknown encoder {manifest['encoder']!r}")
    if not isinstance(manifest["symmetry_break"], bool):
        problems.append(f"{label} symmetry_break must be true or false")
    if not is_hex(manifest["cubes_sha256"], 64):
        problems.append(f"{label} cubes_sha256 is not a sha256 hex digest")
    # Bookkeeping the gate cannot check: it runs on a checkout that need not be
    # a git repository. It is required to be well formed so that it can be
    # looked up by hand, and it is never treated as evidence.
    if not is_hex(manifest["snapshot_commit"], 40):
        problems.append(f"{label} snapshot_commit is not a 40-character commit id")
    base = manifest["base"]
    if not isinstance(base, dict) or set(base) != MANIFEST_BASE_KEYS:
        problems.append(f"{label} base must hold exactly {sorted(MANIFEST_BASE_KEYS)}")
    else:
        for field in ("n_vars", "n_clauses"):
            if isinstance(base[field], bool) or not isinstance(base[field], int):
                problems.append(f"{label} base.{field} must be an integer")
        if not is_hex(base["sha256"], 64):
            problems.append(f"{label} base.sha256 is not a sha256 hex digest")
    if problems:
        report_examples(report, "W1", claim_id, problems)
        return None
    return manifest


def collect_verdict(
    document: object, where: str, label: str, n_cubes: int,
    verdicts: dict[int, Verdict], problems: list[str],
) -> None:
    """Check one verdict object and file it under its cube id.

    One implementation for both storage forms, so that "a duplicate cube is
    refused" cannot be true of a directory of verdicts and false of a
    consolidated file holding the same objects.

    The two proof keys may be missing, and a cube that is missing them is
    reported as having no proof digest rather than no verdict.
    """
    if not isinstance(document, dict):
        problems.append(f"{label}: {where} is not an object")
        return
    unknown = sorted(set(document) - VERDICT_KEYS)
    missing = sorted(VERDICT_REQUIRED_KEYS - set(document))
    if unknown or missing:
        problems.append(
            f"{label}: {where} keys are wrong: unknown {unknown}, missing {missing} "
            f"(a verdict is {sorted(VERDICT_REQUIRED_KEYS)}, and may add "
            f"{sorted(VERDICT_PROOF_KEYS)})"
        )
        return
    cube = document["cube"]
    if isinstance(cube, bool) or not isinstance(cube, int) or not 0 <= cube < n_cubes:
        problems.append(f"{label}: {where} has cube {cube!r}, outside 0..{n_cubes - 1}")
        return
    if cube in verdicts:
        problems.append(f"{label}: cube {cube} has more than one verdict on record")
        return
    lits = document["lits"]
    if not isinstance(lits, list) or not all(
        isinstance(x, int) and not isinstance(x, bool) for x in lits
    ):
        problems.append(f"{label}: verdict for cube {cube} has no list of literals")
        return
    verdicts[cube] = Verdict(
        list(lits), document["rc"], document.get("drat_sha256"), document.get("drat_bytes")
    )


def read_verdicts_dir(
    root: Path, path: Path, label: str, n_cubes: int, report: Report,
) -> tuple[dict[int, Verdict], list[str]] | None:
    """One JSON file per cube. Returns ``(verdicts, problems)``, or None if the
    path is not a directory at all."""
    if not path.is_dir():
        return None
    verdicts: dict[int, Verdict] = {}
    problems: list[str] = []
    for entry in sorted(path.iterdir()):
        if entry.is_dir() or entry.suffix.lower() != ".json":
            report.warn(f"{entry.relative_to(root).as_posix()} is not a wave verdict")
            continue
        try:
            document = load_json(entry)
        except (ValueError, UnicodeDecodeError) as exc:
            problems.append(f"{label}: verdict {entry.name} will not parse: {exc}")
            continue
        collect_verdict(document, f"verdict {entry.name}", label, n_cubes, verdicts, problems)
    return verdicts, problems


def read_verdicts_jsonl(
    path: Path, label: str, n_cubes: int,
) -> tuple[dict[int, Verdict], list[str]] | None:
    """The same verdict objects consolidated one per line.

    A wave of sixteen thousand cubes is sixteen thousand files, which is not a
    thing to put in a repository, so a wave may commit its verdicts as one
    ``.jsonl``. Nothing about W3 changes: every cube id still has to appear
    exactly once with rc 20 and its own literals.

    Every line has to be a verdict. A line that will not parse is a failure and
    never a skip - a reader that skips one is a reader that passes a wave with
    a cube nobody can account for, which is the exact hole W3 exists to close.
    """
    if not path.is_file():
        return None
    try:
        text = path.read_bytes().decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        return {}, [f"{label}: verdicts will not read as ASCII: {exc}"]
    verdicts: dict[int, Verdict] = {}
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        where = f"verdicts line {number}"
        if not raw.strip():
            problems.append(f"{label}: {where} is empty; the file is one verdict per line")
            continue
        try:
            document = json.loads(raw)
        except ValueError as exc:
            problems.append(f"{label}: {where} will not parse: {exc}")
            continue
        collect_verdict(document, where, label, n_cubes, verdicts, problems)
    return verdicts, problems


def read_verdicts(
    root: Path, claim_id: str, label: str, verdicts_rel: object, wave_dir: str,
    n_cubes: int, report: Report,
) -> dict[int, Verdict] | None:
    """W3, first half: load one verdict per cube id, or say what is wrong.

    The verdicts are read from inside ``wave_dir`` - the directory this wave's
    own manifest names - and from nowhere else under ``evidence/waves/``. They
    come in either of two forms, and the *recorded name* decides which: a path
    ending ``.jsonl`` is the consolidated file, anything else is a directory of
    one file per cube. Reading the claim rather than the disk is deliberate - a
    claim then means one thing whatever happens to be lying about, and a
    directory that has been given a ``.jsonl`` name is refused rather than
    quietly accepted.
    """
    try:
        path = artifact_path(root, verdicts_rel, f"{label} verdicts", wave_dir)
    except ArtifactPathError as exc:
        report.fail("W3", claim_id, str(exc))
        return None

    consolidated = isinstance(verdicts_rel, str) and verdicts_rel.endswith(VERDICTS_SUFFIX)
    if consolidated:
        loaded = read_verdicts_jsonl(path, label, n_cubes)
        wanted = "a file"
    else:
        loaded = read_verdicts_dir(root, path, label, n_cubes, report)
        wanted = "a directory"
    if loaded is None:
        report.fail("W3", claim_id, f"{label} verdicts {verdicts_rel} is not {wanted}")
        return None

    verdicts, problems = loaded
    if problems:
        report_examples(report, "W3", claim_id, problems)
        return None
    return verdicts


def check_transcripts(
    root: Path, claim_id: str, label: str, transcripts_rel: object, wave_dir: str,
    verdicts: dict[int, Verdict], n_cubes: int, report: Report,
) -> dict[int, tuple[Path, str]] | None:
    """W4: one transcript line per cube, each about this cube's proof.

    Returns ``{cube: (proof path, proof sha256)}`` when the whole set is sound,
    else None. The gate never opens a proof here - a ``.drat.gz`` is the
    checker's business, and ``--reverify-drat`` is where a checker is run.

    The transcripts file and every proof it names belong to this wave, so both
    are required inside ``wave_dir``: a line pointing at another wave's proof
    records a checker reading somebody else's work.
    """
    try:
        path = artifact_path(root, transcripts_rel, f"{label} transcripts", wave_dir)
    except ArtifactPathError as exc:
        report.fail("W4", claim_id, str(exc))
        return None
    if not path.is_file():
        report.fail("W4", claim_id, f"{label} transcripts {transcripts_rel} does not exist")
        return None
    try:
        text = path.read_bytes().decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        report.fail("W4", claim_id, f"{label} transcripts will not read as ASCII: {exc}")
        return None

    entries: dict[int, dict] = {}
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError as exc:
            problems.append(f"{label}: transcript line {number} will not parse: {exc}")
            continue
        if not isinstance(line, dict) or set(line) != TRANSCRIPT_KEYS:
            problems.append(
                f"{label}: transcript line {number} keys are not exactly "
                f"{sorted(TRANSCRIPT_KEYS)}"
            )
            continue
        cube = line["cube"]
        if isinstance(cube, bool) or not isinstance(cube, int) or not 0 <= cube < n_cubes:
            problems.append(
                f"{label}: transcript line {number} has cube {cube!r}, outside 0..{n_cubes - 1}"
            )
            continue
        if cube in entries:
            problems.append(f"{label}: cube {cube} has more than one transcript line")
            continue
        entries[cube] = line

    proofs: dict[int, tuple[Path, str]] = {}
    for cube in range(n_cubes):
        line = entries.get(cube)
        if line is None:
            problems.append(f"{label}: cube {cube} has no transcript line")
            continue
        verdict = verdicts.get(cube)
        if verdict is not None:
            if not is_hex(line["drat_sha256"], 64) or line["drat_sha256"] != verdict.drat_sha256:
                problems.append(
                    f"{label}: cube {cube} transcript records proof sha256 "
                    f"{str(line['drat_sha256'])[:16]}, the verdict records "
                    f"{str(verdict.drat_sha256)[:16]}"
                )
                continue
            if line["drat_bytes"] != verdict.drat_bytes:
                problems.append(
                    f"{label}: cube {cube} transcript and verdict disagree on the proof size"
                )
                continue
        if line["verdict"] != "s VERIFIED":
            problems.append(
                f"{label}: cube {cube} transcript verdict is {line['verdict']!r}, not 's VERIFIED'"
            )
            continue
        if not isinstance(line["checker"], str) or not line["checker"].strip():
            problems.append(f"{label}: cube {cube} transcript names no checker")
            continue
        try:
            proof = artifact_path(
                root, line["proof_path_rel"], f"{label} cube {cube} proof",
                wave_dir, committed=False,
            )
        except ArtifactPathError as exc:
            problems.append(str(exc))
            continue
        if not str(line["proof_path_rel"]).endswith(PROOF_SUFFIX):
            problems.append(
                f"{label}: cube {cube} proof {line['proof_path_rel']} does not end {PROOF_SUFFIX}"
            )
            continue
        proofs[cube] = (proof, line["drat_sha256"])

    if problems:
        report_examples(report, "W4", claim_id, problems)
        return None
    return proofs


def reverify_wave(
    root: Path, claim_id: str, label: str, manifest: dict, split: list[int],
    proofs: dict[int, tuple[Path, str]], report: Report,
) -> bool:
    """Re-run the checker on every proof this wave still has on disk.

    Each proof is decompressed into a temp directory under the repository's
    gitignored ``scratch/``, hashed against what the transcript recorded, and
    fed to the checker with the cube instance rebuilt beside it. This is a full
    re-check: one cube instance is written per proof, so it costs the base
    instance once per cube. On a wave of thousands that is a job for a machine
    with time, which is why it is opt-in and CI never asks for it.
    """
    binary = find_drat_trim()
    if binary is None:
        report.info(f"{claim_id}: --reverify-drat asked for, no drat-trim binary found")
        return True

    scratch = root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    module = ENCODERS[manifest["encoder"]]
    absent = 0
    with tempfile.TemporaryDirectory(prefix="nk2wave-", dir=scratch) as work:
        workdir = Path(work)
        n_vars, clauses = module.build(
            int(manifest["N"]), int(manifest["k"]), int(manifest["l"]),
            symmetry_break=bool(manifest["symmetry_break"]),
        )
        base = write_cnf(workdir / "base.cnf", n_vars, clauses)
        for cube in sorted(proofs):
            proof_gz, recorded_sha = proofs[cube]
            if not proof_gz.is_file():
                absent += 1
                continue
            try:
                with gzip.open(proof_gz, "rb") as source:
                    raw = source.read()
            except (OSError, EOFError) as exc:  # BadGzipFile is an OSError
                report.fail(
                    "W4", claim_id, f"{label}: cube {cube} proof will not decompress: {exc}"
                )
                return False
            if sha256_bytes(raw) != recorded_sha:
                report.fail(
                    "W4", claim_id,
                    f"{label}: cube {cube} proof on disk is not the one the transcript records",
                )
                return False
            proof_path = workdir / "cube.drat"
            proof_path.write_bytes(raw)
            instance = write_cube_cnf(base["path"], split, cube, workdir / "cube.cnf")
            completed = subprocess.run(
                [binary, str(instance["path"]), str(proof_path)],
                capture_output=True, text=True, check=False,
            )
            if completed.returncode != 0 or "s VERIFIED" not in completed.stdout:
                report.fail(
                    "W4", claim_id,
                    f"{label}: cube {cube} did not re-verify (rc {completed.returncode})",
                )
                return False
    if absent:
        report.info(f"{claim_id}: {label}: {absent} proof(s) are not on disk and were not re-run")
    return True


def verify_wave(
    root: Path, claim_id: str, label: str, manifest_rel: object, verdicts_rel: object,
    transcripts_rel: object, reverify: bool, report: Report,
) -> WaveCheck:
    """W1 to W4 over one wave block."""
    # First, which directory *is* this wave. Everything below is read from
    # inside it, so that the solving a claim rests on cannot be another wave's.
    wave_dir = wave_directory(root, claim_id, label, manifest_rel, report)
    if wave_dir is None:
        return WaveCheck(False, None, False)
    manifest = read_manifest(root, claim_id, label, manifest_rel, report)
    if manifest is None:
        return WaveCheck(False, None, False)

    # W1: the split has to be a set of distinct main variables. var(x_n) = n in
    # every encoder, so 1..N are the only variables whose meaning is shared;
    # splitting on an auxiliary would make the cube set encoder-specific and the
    # confirming wave could not use it.
    try:
        check_split(manifest["split_vars"], int(manifest["N"]))
    except CubeError as exc:
        report.fail("W1", claim_id, f"{label} split is not usable: {exc}")
        return WaveCheck(False, manifest, False)
    split = list(manifest["split_vars"])
    n_cubes = 1 << len(split)
    if manifest["n_cubes"] != n_cubes:
        report.fail(
            "W1", claim_id,
            f"{label} records {manifest['n_cubes']} cubes; a split of {len(split)} variables "
            f"has {n_cubes}",
        )
        return WaveCheck(False, manifest, False)

    # W1: the base instance every cube was derived from has to regenerate here.
    try:
        sha, n_vars, n_clauses = regenerate(
            int(manifest["N"]), int(manifest["k"]), int(manifest["l"]),
            str(manifest["encoder"]), bool(manifest["symmetry_break"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        report.fail("W1", claim_id, f"{label} base instance will not regenerate: {exc}")
        return WaveCheck(False, manifest, False)
    base = manifest["base"]
    if (sha, n_vars, n_clauses) != (base["sha256"], base["n_vars"], base["n_clauses"]):
        report.fail(
            "W1", claim_id,
            f"{label} base instance does not regenerate to what the manifest records: "
            f"recorded {str(base['sha256'])[:16]} / {base['n_vars']} vars / "
            f"{base['n_clauses']} clauses, regenerated {sha[:16]} / {n_vars} / {n_clauses}",
        )
        return WaveCheck(False, manifest, False)

    # W2: completeness by construction. The cube set is re-derived from the
    # split and hashed; the file the wave actually ran from is never read, so a
    # cube set that skips a case cannot agree with a hash that covers them all.
    if manifest["cube_construction"] != CONSTRUCTION:
        report.fail(
            "W2", claim_id,
            f"{label} was cut by {manifest['cube_construction']!r}; this gate re-derives "
            f"{CONSTRUCTION!r} and refuses to guess at another construction",
        )
        return WaveCheck(False, manifest, False)
    derived = cubes_sha256(split)
    if derived != manifest["cubes_sha256"]:
        report.fail(
            "W2", claim_id,
            f"{label} cube set sha256 mismatch: recorded {str(manifest['cubes_sha256'])[:16]}, "
            f"re-derived from split_vars {derived[:16]} - the cubes that ran are not every "
            "assignment of this split",
        )
        return WaveCheck(False, manifest, False)

    # W3: every cube id, every one of them rc 20, each over its own literals.
    verdicts = read_verdicts(root, claim_id, label, verdicts_rel, wave_dir, n_cubes, report)
    if verdicts is None:
        return WaveCheck(False, manifest, False)
    problems: list[str] = []
    for cube in range(n_cubes):
        verdict = verdicts.get(cube)
        if verdict is None:
            problems.append(f"{label}: cube {cube} has no verdict")
            continue
        if verdict.rc != 20 or isinstance(verdict.rc, bool):
            problems.append(
                f"{label}: cube {cube} came back rc {verdict.rc!r}; only rc 20 is UNSAT, and a "
                "cube that is not UNSAT leaves the decomposition undecided"
            )
            continue
        if verdict.lits != cube_literals(split, cube):
            problems.append(
                f"{label}: cube {cube} records lits {verdict.lits}, the construction gives "
                f"{cube_literals(split, cube)}"
            )
    if problems:
        report_examples(report, "W3", claim_id, problems)
        return WaveCheck(False, manifest, False)

    # W4: transcripts are optional. Absent, the wave stands on solver verdicts.
    if transcripts_rel is None:
        return WaveCheck(True, manifest, False)
    proofs = check_transcripts(
        root, claim_id, label, transcripts_rel, wave_dir, verdicts, n_cubes, report
    )
    if proofs is None:
        return WaveCheck(True, manifest, False)
    if reverify and not reverify_wave(root, claim_id, label, manifest, split, proofs, report):
        return WaveCheck(True, manifest, False)
    return WaveCheck(True, manifest, True)


# --- W5 ---------------------------------------------------------------------


def rule_w5(claim: dict, claim_id: str, wave: dict | None, manifest: dict | None,
            report: Report) -> None:
    """The claim and the wave have to be about the same instance, and the kind
    has to say so."""
    kind = claim["kind"]
    if wave is None:
        if kind == "upper_bound_wave":
            report.fail("W5", claim_id, "kind upper_bound_wave carries no wave")
        return
    if kind == "lower_bound":
        report.fail(
            "W5", claim_id,
            "a lower bound is established by a witness, not by an UNSAT decomposition; "
            "this claim carries a wave",
        )
    elif kind == "upper_bound":
        report.fail(
            "W5", claim_id,
            "an upper bound resting on a cube wave is declared kind upper_bound_wave, so that "
            "what carries it is visible in the claim itself",
        )
    if manifest is None:
        return
    want = (claim["value"], claim["k"], claim["l"])
    got = (manifest["N"], manifest["k"], manifest["l"])
    if got != want:
        report.fail(
            "W5", claim_id, f"wave manifest is (N,k,l)={got}, the claim needs {want}"
        )


# --- W6 ---------------------------------------------------------------------


def rule_w6(
    claim: dict, claim_id: str, root: Path, wave: dict, primary: WaveCheck,
    run_encoders: set[str], reverify: bool, report: Report,
) -> bool:
    """No exact claim rests on one encoding, wave or no wave.

    A DRAT proof certifies that a CNF is unsatisfiable. It says nothing about
    whether that CNF is the problem, so a wave - however completely decomposed
    and however thoroughly proof-checked - carries exactly one encoder's opinion
    of what avoidance means. G3 answers that for monolithic runs by demanding
    two of them; this is the same demand, in the shape a wave comes in.
    """
    confirm = wave["confirm"]
    primary_encoder = primary.manifest["encoder"] if primary.manifest else None
    if confirm is None:
        if claim["kind"] == "exact":
            report.fail(
                "W6", claim_id,
                "an exact claim resting on a cube wave needs wave.confirm: either a second "
                "complete wave from a different encoder, or unsat_runs from one. Declaring a "
                "lower evidence_level does not buy the word 'exact'",
            )
        return False

    if confirm["kind"] == "unsat_runs":
        others = sorted(run_encoders - {primary_encoder})
        if not others:
            report.fail(
                "W6", claim_id,
                f"wave.confirm names unsat_runs, but no verified run-log uses an encoder other "
                f"than the wave's {primary_encoder!r}",
            )
            return False
        return True

    second = verify_wave(
        root, claim_id, "confirm wave", confirm["manifest"], confirm["verdicts_dir"],
        confirm["transcripts"], reverify, report,
    )
    if not second.ok or second.manifest is None:
        return False
    if second.manifest["encoder"] == primary_encoder:
        report.fail(
            "W6", claim_id,
            f"the confirming wave uses the same encoder ({primary_encoder!r}); a second run of "
            "one encoding confirms nothing about the encoding",
        )
        return False
    want = (claim["value"], claim["k"], claim["l"])
    got = (second.manifest["N"], second.manifest["k"], second.manifest["l"])
    if got != want:
        report.fail(
            "W6", claim_id, f"the confirming wave is (N,k,l)={got}, the claim needs {want}"
        )
        return False
    return True


def wave_evidence_level(transcripts_verified: bool, confirmed: bool) -> int:
    """Where a verified wave lands on the ladder in LEVELS."""
    if confirmed:
        return LEVEL_DRAT_TRANSCRIPT if transcripts_verified else LEVEL_UNSAT_DUAL
    return LEVEL_WAVE_DRAT if transcripts_verified else LEVEL_UNSAT_WAVE


# --- driver -----------------------------------------------------------------


def referenced_paths(claims: list[dict]) -> tuple[set[str], set[str]]:
    """Paths claims point at, and directory prefixes they point into.

    A wave is referenced as a unit: the manifest names a directory that holds
    the verdicts, the transcripts and, while they last, the proofs. Listing
    every file of it individually would put tens of thousands of strings in a
    claim to no purpose.
    """
    seen: set[str] = set()
    prefixes: set[str] = set()
    for claim in claims:
        witness = claim.get("witness")
        if isinstance(witness, dict) and isinstance(witness.get("path"), str):
            seen.add(witness["path"])
        runs = claim.get("unsat_runs")
        if isinstance(runs, list):
            seen.update(run for run in runs if isinstance(run, str))
        drat = claim.get("drat")
        if isinstance(drat, dict) and isinstance(drat.get("transcript"), str):
            seen.add(drat["transcript"])
        wave = claim.get("wave")
        if not isinstance(wave, dict):
            continue
        for block in (wave, wave.get("confirm")):
            if not isinstance(block, dict):
                continue
            for key in ("manifest", "verdicts_dir", "transcripts"):
                value = block.get(key)
                if not isinstance(value, str) or not value:
                    continue
                seen.add(value)
                if key == "manifest":
                    parent = PurePosixPath(value).parent.as_posix()
                    if parent not in (".", "", "/"):
                        prefixes.add(parent)
                elif key == "verdicts_dir":
                    prefixes.add(value)
    return seen, prefixes


def warn_unreferenced(root: Path, claims: list[dict], report: Report) -> None:
    referenced, prefixes = referenced_paths(claims)
    if not (root / "evidence").is_dir():
        return
    for path in scanned_files(root):
        rel = path.relative_to(root).as_posix()
        if not rel.startswith("evidence/") or rel in referenced:
            continue
        if any(rel.startswith(f"{prefix}/") for prefix in prefixes):
            continue
        report.warn(f"artifact {rel} is referenced by no claim")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python gate/verify_all.py",
        description="Re-derive every claim in claims/ from the artifacts on disk.",
    )
    parser.add_argument("--root", default=None, help="repository root; default: this checkout")
    parser.add_argument("--claims", default=None, help="claims directory; default: <root>/claims")
    parser.add_argument(
        "--reverify-drat", action="store_true",
        help="re-run drat-trim on any recorded proof that is present on disk",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    claims_dir = Path(args.claims).resolve() if args.claims else root / "claims"
    report = Report()

    rule_g5_anchors(claims_dir, report)

    claims_path = claims_dir / "CLAIMS.json"
    document: object = None
    if not claims_path.is_file():
        report.fail("G1", "-", f"{claims_path.name} does not exist")
    else:
        try:
            document = load_json(claims_path)
        except (ValueError, UnicodeDecodeError) as exc:
            report.fail("G1", "-", f"CLAIMS.json will not parse: {exc}")

    claims: list[dict] = []
    if isinstance(document, dict):
        if set(document) != {"schema", "claims"} or document.get("schema") != "nk2.claims.v1":
            report.fail("G1", "-", "CLAIMS.json must have exactly schema and claims")
        raw = document.get("claims")
        if isinstance(raw, list):
            claims = [c for c in raw if isinstance(c, dict)]
            if len(claims) != len(raw):
                report.fail("G1", "-", "every entry of claims must be an object")
        else:
            report.fail("G1", "-", "claims must be a list")
    elif document is not None:
        report.fail("G1", "-", "CLAIMS.json must be an object")

    seen_ids: set[str] = set()
    for index, claim in enumerate(claims):
        claim_id = rule_g1(claim, index, report)
        if claim_id is None:
            continue
        if claim_id in seen_ids:
            report.fail("G1", claim_id, "duplicate claim id")
            continue
        seen_ids.add(claim_id)

        has_witness = rule_g2(claim, claim_id, root, report)
        dual, instance_shas, run_encoders = rule_g3(claim, claim_id, root, report)
        drat_level = rule_g4(
            claim, claim_id, root, instance_shas, args.reverify_drat, report
        )

        wave = claim.get("wave")
        wave_level = 0
        if wave is None:
            rule_w5(claim, claim_id, None, None, report)
        else:
            primary = verify_wave(
                root, claim_id, "wave", wave["manifest"], wave["verdicts_dir"],
                wave["transcripts"], args.reverify_drat, report,
            )
            rule_w5(claim, claim_id, wave, primary.manifest, report)
            confirmed = rule_w6(
                claim, claim_id, root, wave, primary, run_encoders, args.reverify_drat, report
            )
            if primary.ok:
                wave_level = wave_evidence_level(primary.transcripts_verified, confirmed)

        rule_g5_claim(claim, claim_id, report)

        achieved = 0
        if has_witness:
            achieved = LEVEL_WITNESS
        if dual:
            achieved = max(achieved, LEVEL_UNSAT_DUAL)
        if drat_level and dual:
            # A DRAT proof certifies an UNSAT run, so it can only lift a claim
            # that already has the two encoders G3 requires behind it.
            achieved = max(achieved, drat_level)
        if wave_level:
            achieved = max(achieved, wave_level)
        # G7 only speaks when nothing else about the claim is already broken,
        # so one bad fixture reports one rule rather than a cascade.
        if report.clean(claim_id):
            rule_g7(claim, claim_id, achieved, report)

    rule_g6(root, report)
    warn_unreferenced(root, claims, report)

    if report.failures:
        print(f"\n{report.failures} failure(s); this repository asserts nothing.")
        return 1
    print(f"OK {len(claims)} claim(s) verified from artifacts on disk.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
