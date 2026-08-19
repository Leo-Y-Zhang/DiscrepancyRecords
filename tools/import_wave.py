"""Import a finished off-repo cube-and-conquer wave as committable evidence.

A wave runs outside this repository, because it has to: sixteen thousand cube
instances and tens of gigabytes of DRAT proofs are not a git tree. What the
repository is entitled to keep is the part that can be checked cold - the
manifest, one verdict per cube, and the checker's line per proof - and this tool
is what moves that across.

    python tools/import_wave.py --source <dir> --name <wave-name> [--dry-run]

It refuses more often than it writes, and that is the point. A wave is only
evidence if it is *complete*: every cube of the split decided, every one of them
rc 20, each over the literals the construction gives for its index. So the whole
source is read and checked before a single byte is written, and a wave that is
still running, has a cube that timed out, or carries a cube set the split does
not produce is refused with the cube numbers named. Nothing is imported
half-imported.

Three rules it does not bend:

* **The source is never modified.** A campaign is usually still writing to it.
* **Nothing is written outside** ``evidence/waves/<name>/``.
* **No proof is ever copied.** ``.drat`` and ``.drat.gz`` are gitignored bulk;
  the transcripts are the record of them, and W4 checks transcripts rather than
  proofs precisely so the proofs can be deleted.

The verdicts land as one ``verdicts.jsonl`` - the same objects the per-cube
files hold, one per line, sorted by cube - because sixteen thousand committed
files is a repository nobody can clone, browse or review. ``gate/verify_all.py``
reads either form and holds every W rule identically over both.

What this tool does *not* do is regenerate the base instance: that is W1's job,
it costs a full encode of the instance, and the gate does it on every run. Import
a wave, then run the gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python tools/import_wave.py` from a checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate.verify_all import (  # noqa: E402
    ENCODERS,
    EXAMPLES,
    LEVEL_DRAT_TRANSCRIPT,
    LEVEL_UNSAT_DUAL,
    LEVEL_UNSAT_WAVE,
    LEVEL_WAVE_DRAT,
    LEVELS,
    MANIFEST_BASE_KEYS,
    MANIFEST_KEYS,
    MANIFEST_SCHEMA,
    TRANSCRIPT_KEYS,
    VERDICT_KEYS,
    WAVES_DIR,
    is_hex,
)
from nk2.cubes import (  # noqa: E402
    CONSTRUCTION,
    CubeError,
    check_split,
    cube_literals,
    cubes_sha256,
)

# The checker writes its own record, not the gate's: it knows what it ran, how
# long it took and against which cube instance, and it has never heard of
# `evidence/waves/`. Both spellings are accepted and normalised to the gate's.
SOURCE_TRANSCRIPT_KEYS = {
    "cube", "ok", "tool", "tool_rc", "verdict", "drat_sha256", "drat_bytes",
    "cnf_sha256", "check_wall_s",
}
VERIFIED = "s VERIFIED"

# Where a cube's proof belongs once its wave is in the repository. Nothing is
# copied there - the proofs are gitignored bulk and usually deleted as soon as
# a checker has read them - so this records the path the proof had, and would
# have again, rather than asserting that a file is present. The gate never
# opens a proof under W4, and `--reverify-drat` reports the absent ones by
# count.
PROOF_NAME = "cube_{:05d}.drat.gz"
PROOFS_SUBDIR = "proofs"
VERDICTS_NAME = "verdicts.jsonl"
TRANSCRIPTS_NAME = "transcripts.jsonl"
MANIFEST_NAME = "manifest.json"

# A wave name becomes a directory name, so it may not be a path.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# The two fields of a claim nobody can generate: what is published about this
# quantity, and what actually ran. Left visibly unfilled rather than invented.
REPLACE = "REPLACE:"
CONFIRM_PLACEHOLDER = "CONFIRMING-WAVE"


class WaveImportError(Exception):
    """The source is not a complete wave, or the destination is not free."""


def fail(message: str) -> None:
    raise WaveImportError(message)


def examples(problems: list[str]) -> str:
    """The first few problems and a count, the way the gate reports a wave."""
    shown = problems[:EXAMPLES]
    if len(problems) > EXAMPLES:
        shown.append(f"... and {len(problems) - EXAMPLES} more like it")
    return "\n  ".join(shown)


def read_json_file(path: Path, what: str) -> object:
    if not path.is_file():
        fail(f"{what} {path.name} does not exist under the source directory")
    try:
        return json.loads(path.read_bytes().decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        fail(f"{what} {path.name} will not parse: {exc}")
    return None  # unreachable; fail() raises


# --- the manifest -----------------------------------------------------------


def load_manifest(source: Path) -> dict:
    """Read the source manifest and check everything about it that does not
    need the verdicts."""
    manifest = read_json_file(source / MANIFEST_NAME, "manifest")
    if not isinstance(manifest, dict):
        fail("manifest.json is not an object")
    if set(manifest) != MANIFEST_KEYS:
        unknown = sorted(set(manifest) - MANIFEST_KEYS)
        missing = sorted(MANIFEST_KEYS - set(manifest))
        fail(f"manifest keys are wrong: unknown {unknown}, missing {missing}")
    if manifest["schema"] != MANIFEST_SCHEMA:
        fail(
            f"manifest is schema {manifest['schema']!r}; the gate reads "
            f"{MANIFEST_SCHEMA!r} and refuses to guess at another"
        )

    for field in ("N", "k", "l", "n_cubes"):
        value = manifest[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            fail(f"manifest {field} must be a positive integer, got {value!r}")
    if manifest["encoder"] not in ENCODERS:
        fail(f"manifest names unknown encoder {manifest['encoder']!r}")
    if not isinstance(manifest["symmetry_break"], bool):
        fail("manifest symmetry_break must be true or false")
    if not is_hex(manifest["snapshot_commit"], 40):
        fail("manifest snapshot_commit is not a 40-character commit id")
    base = manifest["base"]
    if not isinstance(base, dict) or set(base) != MANIFEST_BASE_KEYS:
        fail(f"manifest base must hold exactly {sorted(MANIFEST_BASE_KEYS)}")
    for field in ("n_vars", "n_clauses"):
        if isinstance(base[field], bool) or not isinstance(base[field], int):
            fail(f"manifest base.{field} must be an integer")
    if not is_hex(base["sha256"], 64):
        fail("manifest base.sha256 is not a sha256 hex digest")

    try:
        check_split(manifest["split_vars"], int(manifest["N"]))
    except CubeError as exc:
        fail(f"manifest split_vars is not a usable split: {exc}")
    split = list(manifest["split_vars"])
    if manifest["n_cubes"] != 1 << len(split):
        fail(
            f"manifest n_cubes is {manifest['n_cubes']}; a split of {len(split)} variables "
            f"has {1 << len(split)} cubes"
        )
    if manifest["cube_construction"] != CONSTRUCTION:
        fail(
            f"manifest was cut by {manifest['cube_construction']!r}; this tool and the gate "
            f"re-derive {CONSTRUCTION!r} and refuse to guess at another construction"
        )
    # The same check the gate makes, made before anything is written: a
    # generator that dropped a case writes a shorter cube file and a hash that
    # matches it, so the hash is only worth anything re-derived from the split.
    derived = cubes_sha256(split)
    if derived != manifest["cubes_sha256"]:
        fail(
            f"manifest cubes_sha256 is {str(manifest['cubes_sha256'])[:16]}, re-derived from "
            f"split_vars it is {derived[:16]}: the cubes that ran are not every assignment "
            "of this split"
        )
    return manifest


# --- the verdicts -----------------------------------------------------------


def load_verdicts(source: Path, n_cubes: int) -> dict[int, dict]:
    """Every verdict in the source, keyed by cube, in either storage form."""
    directory = source / "verdicts"
    consolidated = source / VERDICTS_NAME
    documents: list[tuple[str, object]] = []
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if path.is_dir() or path.suffix.lower() != ".json":
                continue
            documents.append((f"verdict {path.name}", read_json_file(path, "verdict")))
    elif consolidated.is_file():
        try:
            text = consolidated.read_bytes().decode("ascii")
        except (OSError, UnicodeDecodeError) as exc:
            fail(f"{VERDICTS_NAME} will not read as ASCII: {exc}")
        for number, raw in enumerate(text.splitlines(), start=1):
            where = f"{VERDICTS_NAME} line {number}"
            if not raw.strip():
                fail(f"{where} is empty; the file is one verdict per line")
            try:
                documents.append((where, json.loads(raw)))
            except ValueError as exc:
                fail(f"{where} will not parse: {exc}")
    else:
        fail(f"the source has neither a verdicts/ directory nor a {VERDICTS_NAME}")

    verdicts: dict[int, dict] = {}
    problems: list[str] = []
    for where, document in documents:
        if not isinstance(document, dict) or set(document) != VERDICT_KEYS:
            problems.append(f"{where} keys are not exactly {sorted(VERDICT_KEYS)}")
            continue
        cube = document["cube"]
        if isinstance(cube, bool) or not isinstance(cube, int) or not 0 <= cube < n_cubes:
            problems.append(f"{where} has cube {cube!r}, outside 0..{n_cubes - 1}")
            continue
        if cube in verdicts:
            problems.append(f"cube {cube} has more than one verdict in the source")
            continue
        verdicts[cube] = document
    if problems:
        fail("the source verdicts are not readable:\n  " + examples(problems))
    return verdicts


def check_complete(verdicts: dict[int, dict], split: list[int], n_cubes: int) -> None:
    """Refuse anything short of every cube, decided, over its own literals."""
    missing: list[int] = []
    problems: list[str] = []
    for cube in range(n_cubes):
        verdict = verdicts.get(cube)
        if verdict is None:
            missing.append(cube)
            continue
        rc = verdict["rc"]
        if isinstance(rc, bool) or rc != 20:
            problems.append(
                f"cube {cube} came back rc {rc!r}; only rc 20 is UNSAT, and this wave is not "
                "finished until every cube is"
            )
            continue
        lits = verdict["lits"]
        if lits != cube_literals(split, cube):
            problems.append(
                f"cube {cube} records lits {lits}, the construction gives "
                f"{cube_literals(split, cube)}"
            )
            continue
        sha, size = verdict["drat_sha256"], verdict["drat_bytes"]
        if sha is None and size is None:
            continue
        if not is_hex(sha, 64):
            problems.append(f"cube {cube} records drat_sha256 {str(sha)[:16]!r}, not a digest")
        elif isinstance(size, bool) or not isinstance(size, int) or size < 0:
            problems.append(f"cube {cube} records drat_bytes {size!r}, not a byte count")
    if missing:
        problems.insert(
            0,
            f"{len(missing)} cube(s) have no verdict at all, starting "
            + ", ".join(f"cube {cube}" for cube in missing[:EXAMPLES]),
        )
    if problems:
        detail = examples(problems)
        fail(f"this wave is not complete, so it is not evidence of anything:\n  {detail}")


# --- the transcripts --------------------------------------------------------


def load_transcripts(
    source: Path, name: str, verdicts: dict[int, dict], n_cubes: int
) -> list[dict] | None:
    """The checker's record, normalised into the shape W4 reads.

    Returns None when the source carries no transcripts - a wave may stand on
    solver verdicts alone, and then it earns ``unsat-wave`` rather than
    ``wave-drat-verified``.

    Every fact in the output comes from the source except ``proof_path_rel``,
    which is where this wave's proof for that cube belongs in the repository.
    """
    path = source / TRANSCRIPTS_NAME
    if not path.is_file():
        return None
    try:
        text = path.read_bytes().decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        fail(f"{TRANSCRIPTS_NAME} will not read as ASCII: {exc}")

    lines: dict[int, dict] = {}
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        where = f"{TRANSCRIPTS_NAME} line {number}"
        if not raw.strip():
            problems.append(f"{where} is empty; the file is one checked proof per line")
            continue
        try:
            line = json.loads(raw)
        except ValueError as exc:
            problems.append(f"{where} will not parse: {exc}")
            continue
        if not isinstance(line, dict) or set(line) not in (
            SOURCE_TRANSCRIPT_KEYS, TRANSCRIPT_KEYS
        ):
            problems.append(
                f"{where} keys are neither the checker's {sorted(SOURCE_TRANSCRIPT_KEYS)} "
                f"nor the gate's {sorted(TRANSCRIPT_KEYS)}"
            )
            continue
        cube = line["cube"]
        if isinstance(cube, bool) or not isinstance(cube, int) or not 0 <= cube < n_cubes:
            problems.append(f"{where} has cube {cube!r}, outside 0..{n_cubes - 1}")
            continue
        if cube in lines:
            problems.append(f"cube {cube} has more than one transcript line in the source")
            continue
        lines[cube] = line
    if problems:
        fail("the source transcripts are not readable:\n  " + examples(problems))

    normalised: list[dict] = []
    for cube in range(n_cubes):
        line = lines.get(cube)
        if line is None:
            problems.append(f"cube {cube} has no transcript line")
            continue
        if "ok" in line and line["ok"] is not True:
            problems.append(f"cube {cube} has a transcript whose ok is {line['ok']!r}, not true")
            continue
        if line["verdict"] != VERIFIED:
            problems.append(
                f"cube {cube} transcript verdict is {line['verdict']!r}, not {VERIFIED!r}"
            )
            continue
        checker = line["tool"] if "tool" in line else line["checker"]
        if not isinstance(checker, str) or not checker.strip():
            problems.append(f"cube {cube} transcript names no checker")
            continue
        verdict = verdicts[cube]
        if line["drat_sha256"] != verdict["drat_sha256"]:
            problems.append(
                f"cube {cube} transcript is about proof sha256 "
                f"{str(line['drat_sha256'])[:16]}, the verdict recorded "
                f"{str(verdict['drat_sha256'])[:16]}"
            )
            continue
        if line["drat_bytes"] != verdict["drat_bytes"]:
            problems.append(
                f"cube {cube} transcript and verdict disagree on the proof size: "
                f"{line['drat_bytes']!r} against {verdict['drat_bytes']!r}"
            )
            continue
        normalised.append(
            {
                "cube": cube,
                "drat_sha256": line["drat_sha256"],
                "drat_bytes": line["drat_bytes"],
                "proof_path_rel": (
                    f"{WAVES_DIR}/{name}/{PROOFS_SUBDIR}/{PROOF_NAME.format(cube)}"
                ),
                "checker": checker,
                "verdict": VERIFIED,
            }
        )
    if problems:
        fail(
            "the source transcripts do not account for this wave's proofs:\n  "
            + examples(problems)
        )
    return normalised


# --- writing ----------------------------------------------------------------


def jsonl_bytes(documents: list[dict]) -> bytes:
    """One JSON object per line, keys sorted, ASCII, LF, one trailing newline."""
    return "".join(
        json.dumps(document, sort_keys=True) + "\n" for document in documents
    ).encode("ascii")


def destination(root: Path, name: str) -> Path:
    if not NAME_PATTERN.match(name):
        fail(
            f"wave name {name!r} is not a plain directory name; a wave is one directory "
            f"under {WAVES_DIR}/, named like k17_l2_N274_totalizer"
        )
    return root / WAVES_DIR / name


def write_files(dest: Path, payloads: list[tuple[Path, bytes]]) -> None:
    """Write the wave, and nothing anywhere else. Every path here is under
    ``dest``, which ``destination`` has already pinned inside the repository."""
    dest.mkdir(parents=True, exist_ok=True)
    for path, payload in payloads:
        path.write_bytes(payload)


# --- what to print ----------------------------------------------------------


def summarise(manifest: dict, verdicts: dict[int, dict], transcripts: list[dict] | None) -> str:
    n_cubes = int(manifest["n_cubes"])
    walls = [v["wall_s"] for v in verdicts.values() if isinstance(v["wall_s"], (int, float))]
    unrecorded = n_cubes - len(walls)
    sizes = [v["drat_bytes"] for v in verdicts.values() if isinstance(v["drat_bytes"], int)]
    total = sum(walls)
    lines = [
        f"  instance: N={manifest['N']} k={manifest['k']} l={manifest['l']} "
        f"encoder={manifest['encoder']} symmetry_break={str(manifest['symmetry_break']).lower()}",
        f"  base:     {manifest['base']['sha256']} "
        f"({manifest['base']['n_vars']} vars, {manifest['base']['n_clauses']} clauses)",
        f"  split:    {manifest['split_vars']}",
        f"  cubes:    {n_cubes}, every one rc 20",
        f"  solver:   {total:.2f} s total ({total / 3600:.2f} h), "
        f"longest cube {max(walls, default=0.0):.2f} s",
    ]
    if unrecorded:
        lines.append(f"            {unrecorded} cube(s) recorded no wall time")
    if sizes:
        lines.append(
            f"  proofs:   {sum(sizes)} bytes across {len(sizes)} cube(s), "
            f"largest {max(sizes)} bytes - none of them copied"
        )
    else:
        lines.append("  proofs:   none recorded")
    if transcripts is None:
        lines.append("  checked:  no transcripts in the source; this wave rests on rc 20 alone")
    else:
        lines.append(
            f"  checked:  {len(transcripts)} transcript line(s), every one {VERIFIED!r}"
        )
    return "\n".join(lines)


def claim_fragment(
    manifest: dict, name: str, transcripts: list[dict] | None, root: Path, confirmed: bool
) -> dict:
    """The claim this wave supports - as an upper bound, or as half an exact."""
    k, l, value = int(manifest["k"]), int(manifest["l"]), int(manifest["N"])
    wave_dir = f"{WAVES_DIR}/{name}"
    level = LEVELS[
        (
            (LEVEL_DRAT_TRANSCRIPT if transcripts is not None else LEVEL_UNSAT_DUAL)
            if confirmed
            else (LEVEL_WAVE_DRAT if transcripts is not None else LEVEL_UNSAT_WAVE)
        )
        - 1
    ]
    confirm = None
    if confirmed:
        confirm = {
            "kind": "wave",
            "manifest": f"{WAVES_DIR}/{CONFIRM_PLACEHOLDER}/{MANIFEST_NAME}",
            "verdicts_dir": f"{WAVES_DIR}/{CONFIRM_PLACEHOLDER}/{VERDICTS_NAME}",
            "transcripts": None,
        }
    witness = None
    if confirmed:
        # An exact claim needs the lower side too, and if this checkout already
        # holds the witness at V-1 there is no reason to make anyone look it up.
        candidate = root / "evidence" / "witnesses" / f"k{k}_l{l}_N{value - 1}.txt"
        if candidate.is_file():
            witness = {
                "path": candidate.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            }
    return {
        "id": f"N{k}_{l}_{'exact' if confirmed else 'upper'}_{value}",
        "k": k,
        "l": l,
        "kind": "exact" if confirmed else "upper_bound_wave",
        "value": value,
        "witness": witness,
        "unsat_runs": [],
        "drat": None,
        "wave": {
            "manifest": f"{wave_dir}/{MANIFEST_NAME}",
            "verdicts_dir": f"{wave_dir}/{VERDICTS_NAME}",
            "transcripts": f"{wave_dir}/{TRANSCRIPTS_NAME}" if transcripts is not None else None,
            "confirm": confirm,
        },
        "evidence_level": level,
        "prior_art": f"{REPLACE} what is published about N({k},{l}), and what this improves on.",
        "notes": (
            f"Cube-and-conquer wave of {manifest['n_cubes']} cubes over split_vars "
            f"{manifest['split_vars']}, encoder {manifest['encoder']}, every cube rc 20. "
            f"Imported by tools/import_wave.py. {REPLACE} solver name and version, machine, "
            "dates, and anything a reader needs that the artifacts do not say."
        ),
    }


def print_fragment(title: str, claim: dict) -> None:
    print(f"--- claim fragment: {title} ---")
    print(json.dumps(claim, indent=2, sort_keys=True))
    print(f"--- end {title} ---")


# --- driver -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python tools/import_wave.py",
        description="Import a completed off-repo cube wave as committable evidence.",
    )
    parser.add_argument("--source", required=True, help="the wave directory a campaign wrote")
    parser.add_argument("--name", required=True, help="the wave's name under evidence/waves/")
    parser.add_argument(
        "--expect-cubes", type=int, default=None,
        help="refuse unless the manifest says this many cubes",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="check everything and write nothing"
    )
    parser.add_argument("--root", default=None, help="repository root; default: this checkout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    source = Path(args.source).resolve()

    try:
        dest = destination(root, args.name)
        if dest.exists() and any(dest.iterdir()):
            fail(
                f"{dest.relative_to(root).as_posix()} already holds a wave; delete it by hand if "
                "it is to be replaced, so that no import can quietly overwrite evidence"
            )
        if not source.is_dir():
            fail(f"source {args.source} is not a directory")

        manifest = load_manifest(source)
        n_cubes = int(manifest["n_cubes"])
        if args.expect_cubes is not None and args.expect_cubes != n_cubes:
            fail(
                f"--expect-cubes {args.expect_cubes} but the manifest says {n_cubes}; one of the "
                "two is about a different wave"
            )
        verdicts = load_verdicts(source, n_cubes)
        check_complete(verdicts, list(manifest["split_vars"]), n_cubes)
        transcripts = load_transcripts(source, args.name, verdicts, n_cubes)
        manifest_bytes = (source / MANIFEST_NAME).read_bytes()
        if b"\r" in manifest_bytes:
            fail("manifest.json holds a CR byte; every artifact in this repository is LF")
    except WaveImportError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    ordered = [verdicts[cube] for cube in range(n_cubes)]
    payloads = [
        (dest / MANIFEST_NAME, manifest_bytes),
        (dest / VERDICTS_NAME, jsonl_bytes(ordered)),
    ]
    if transcripts is not None:
        payloads.append((dest / TRANSCRIPTS_NAME, jsonl_bytes(transcripts)))
    if not args.dry_run:
        write_files(dest, payloads)

    prefix = "DRY RUN: would write" if args.dry_run else "wrote"
    print(f"{prefix} {dest.relative_to(root).as_posix()}/:")
    for path, payload in payloads:
        print(f"  {path.relative_to(root).as_posix()}  ({len(payload)} bytes)")
    print(summarise(manifest, verdicts, transcripts))
    if transcripts is not None:
        print(
            "  note:     transcript lines carry the checker's own verdict, sha256 and byte "
            "count; proof_path_rel names where each proof belongs under the wave, and no "
            "proof was copied."
        )
    print()
    print_fragment(
        "this wave alone (upper_bound_wave)",
        claim_fragment(manifest, args.name, transcripts, root, confirmed=False),
    )
    print()
    print_fragment(
        "with a confirming wave (exact)",
        claim_fragment(manifest, args.name, transcripts, root, confirmed=True),
    )
    print(
        f"\nThe exact form is a template: import the second encoder's wave, put its name in "
        f"place of {CONFIRM_PLACEHOLDER}, and replace every {REPLACE} field. Then run "
        "gate/verify_all.py - nothing here is evidence until it exits 0."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
