"""The gate. The exit code of this script is the only claim this repository makes.

It runs with no SAT solver installed, trusts no cached verdict and reads no
stored "verified" flag. Every claim is re-derived from artifacts on disk:
witnesses are re-evaluated here, and each claimed UNSAT instance is regenerated
from its recorded parameters and compared byte for byte by sha256. A cached
verdict is a promise; regeneration is evidence.

    python gate/verify_all.py
    python gate/verify_all.py --root tests/fixtures/bad_witness_sign

Rules G1 to G7 are documented in docs/TDD.md. Failures print
``FAIL <rule> <claim-id> <reason>`` and set a non-zero exit code. WARN and INFO
lines never change the exit code: an unreferenced artifact is untidiness, and a
claim that understates its evidence is not an error.
"""

from __future__ import annotations

import argparse
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

if __package__ in (None, ""):  # allow `python gate/verify_all.py` from a clean checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nk2 import encode_seqcount, encode_subsets, encode_totalizer  # noqa: E402
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

KINDS = ("lower_bound", "upper_bound", "exact")
LEVELS = ("witness", "unsat-dual", "drat-transcript", "drat-reverified")
CLAIM_KEYS = {
    "id", "k", "l", "kind", "value", "witness", "unsat_runs", "drat",
    "evidence_level", "prior_art", "notes",
}
WITNESS_KEYS = {"path", "sha256"}
DRAT_KEYS = {"proof_sha256", "proof_bytes", "transcript"}

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
EVIDENCE_DIR = "evidence"
# Gitignored wholesale, so a committed artifact never carries one of these.
# Instances and proofs do, which is why they are checked for containment only.
BULK_SUFFIXES = {".cnf", ".drat"}

SKIP_DIR_NAMES = {
    ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "node_modules",
}
# Relative to --root. Fixtures deliberately contain the shapes G6 rejects, so
# they are excluded here and scanned when a fixture is itself the root.
SKIP_RELATIVE = {Path("evidence/drat"), Path("tests/fixtures"), Path("scratch")}
SKIP_SUFFIXES = {".cnf", ".drat", ".tmp", ".pyc"}


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
    missing = CLAIM_KEYS - set(claim)
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


def rule_g3(claim: dict, claim_id: str, root: Path, report: Report) -> tuple[bool, set[str]]:
    """Upper bound V: two distinct encoders, rc 20, and instances that regenerate."""
    needed = claim["kind"] in ("upper_bound", "exact")
    runs = claim["unsat_runs"]
    shas: set[str] = set()
    if not runs:
        if needed:
            report.fail("G3", claim_id, f"{claim['kind']} needs UNSAT run-logs, found none")
        return False, shas

    encoders: set[str] = set()
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
        return False, shas
    return ok, shas


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
        return 3

    binary = find_drat_trim()
    if binary is None:
        report.info(f"{claim_id}: --reverify-drat asked for, no drat-trim binary found")
        return 3
    if proof_path is None or not proof_path.is_file():
        report.info(f"{claim_id}: --reverify-drat asked for, the proof itself is not on disk")
        return 3
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
    return 4


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
        elif kind == "upper_bound" and value < anchor:
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


# --- driver -----------------------------------------------------------------


def referenced_paths(claims: list[dict]) -> set[str]:
    seen: set[str] = set()
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
    return seen


def warn_unreferenced(root: Path, claims: list[dict], report: Report) -> None:
    referenced = referenced_paths(claims)
    if not (root / "evidence").is_dir():
        return
    for path in scanned_files(root):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("evidence/") and rel not in referenced:
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
        dual, instance_shas = rule_g3(claim, claim_id, root, report)
        drat_level = rule_g4(
            claim, claim_id, root, instance_shas, args.reverify_drat, report
        )

        rule_g5_claim(claim, claim_id, report)

        achieved = 0
        if has_witness:
            achieved = 1
        if dual:
            achieved = max(achieved, 2)
        if drat_level and dual:
            # A DRAT proof certifies an UNSAT run, so it can only lift a claim
            # that already has the two encoders G3 requires behind it.
            achieved = max(achieved, drat_level)
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
