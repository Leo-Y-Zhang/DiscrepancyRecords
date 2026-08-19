"""Build a real cube-and-conquer wave small enough to live in a test.

`k=3, l=2, N=9` is the smallest instance this repo has a published answer for:
`N(3,2) = 9`, so the avoidance instance at `N=9` is genuinely UNSAT. Split it on
two variables and it becomes four cubes, each of which this module refutes with
`tests/_minisolve.py` before writing a verdict. That is the honest part: `rc: 20`
is written only for a cube that the UP+DPLL checker in this repository has just
shown has no model, so a broken construction - a cube set that is not a
partition, a unit clause with the wrong sign - cannot produce a passing fixture.

What is synthetic, and stays synthetic, is the provenance. No solver ran, so the
wall clock is a constant; no checker ran, so the transcript's `checker` field
says so in words and the proof bytes behind each `.drat.gz` are a placeholder.
The gate does not open a proof (that is the checker pass, and `--reverify-drat`
is where a checker actually runs), so a placeholder exercises exactly the path
the gate takes and misrepresents nothing it checks.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from nk2 import encode_seqcount, encode_subsets, encode_totalizer
from nk2.cubes import CONSTRUCTION, cube_clauses, cube_literals, cubes_sha256, write_cube_cnf
from nk2.dimacs import write_cnf
from nk2.witness import write_witness
from tests._minisolve import extends

ROOT = Path(__file__).resolve().parent.parent
GOOD = ROOT / "tests" / "fixtures" / "good"

N, K, L = 9, 3, 2
SPLIT = [2, 5]
N_CUBES = 1 << len(SPLIT)

PRIMARY_DIR = "evidence/waves/k3_l2_N9_seqcount"
CONFIRM_DIR = "evidence/waves/k3_l2_N9_totalizer"
MANIFEST = f"{PRIMARY_DIR}/manifest.json"
VERDICTS = f"{PRIMARY_DIR}/verdicts"
VERDICTS_JSONL = f"{PRIMARY_DIR}/verdicts.jsonl"
TRANSCRIPTS = f"{PRIMARY_DIR}/transcripts.jsonl"
PROOFS = f"{PRIMARY_DIR}/proofs"
CONFIRM_MANIFEST = f"{CONFIRM_DIR}/manifest.json"
CONFIRM_VERDICTS = f"{CONFIRM_DIR}/verdicts"
CONFIRM_VERDICTS_JSONL = f"{CONFIRM_DIR}/verdicts.jsonl"

# The two shapes a wave's verdicts come in. `dir` is one JSON file per cube;
# `jsonl` is the same objects consolidated one per line, which is what a wave of
# sixteen thousand cubes has to be committed as.
VERDICT_FORMS = ("dir", "jsonl")

# What a verdict says about the proof of its cube. A wave solved with the
# driver's --no-proof mode kept no proof, so it has no digest to record and
# leaves the pair out; that is the honest shape and not a defect. `null` is the
# older shape, where the keys are present and say nothing - importable as
# unsat-wave, never as drat-verified, because a null is not a digest. A wave
# interrupted in one mode and resumed in the other holds both.
DIGESTS_REAL = "real"
DIGESTS_NULL = "null"
DIGESTS_ABSENT = "absent"
DIGEST_STYLES = (DIGESTS_REAL, DIGESTS_NULL, DIGESTS_ABSENT)

# A syntactically valid commit that is deliberately the null one: the gate
# cannot check that a snapshot commit exists (it runs on a checkout that may not
# be a git repository at all), and a fixture must not pretend otherwise.
NULL_COMMIT = "0" * 40

ENCODERS = {
    "subsets": encode_subsets,
    "seqcount": encode_seqcount,
    "totalizer": encode_totalizer,
}


def write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(document, indent=2, sort_keys=True) + "\n").encode("ascii"))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="ascii"))


def base_instance(encoder: str, symmetry_break: bool) -> dict:
    """Regenerate the base instance and return its recorded block."""
    module = ENCODERS[encoder]
    n_vars, clauses = module.build(N, K, L, symmetry_break=symmetry_break)
    with tempfile.TemporaryDirectory(prefix="wavefix-") as work:
        info = write_cnf(Path(work) / "base.cnf", n_vars, clauses)
    return {
        "n_vars": int(info["n_vars"]),
        "n_clauses": int(info["n_clauses"]),
        "sha256": str(info["sha256"]),
    }


def refute_every_cube(encoder: str, symmetry_break: bool) -> None:
    """Raise unless every cube of the split really has no model."""
    module = ENCODERS[encoder]
    n_vars, clause_iter = module.build(N, K, L, symmetry_break=symmetry_break)
    clauses = [list(c) for c in clause_iter]
    for index in range(N_CUBES):
        if extends(n_vars, clauses + cube_clauses(SPLIT, index), {}):
            raise AssertionError(
                f"cube {index} of the {encoder} wave has a model; the fixture would be a lie"
            )


def manifest_document(encoder: str, symmetry_break: bool) -> dict:
    """The manifest a wave over this fixture's split is entitled to."""
    return {
        "schema": "cube-wave.v2",
        "N": N,
        "k": K,
        "l": L,
        "encoder": encoder,
        "symmetry_break": symmetry_break,
        "snapshot_commit": NULL_COMMIT,
        "base": base_instance(encoder, symmetry_break),
        "split_vars": list(SPLIT),
        "n_cubes": N_CUBES,
        "cubes_sha256": cubes_sha256(SPLIT),
        "cube_construction": CONSTRUCTION,
    }


def placeholder_proof(index: int) -> bytes:
    return f"c placeholder proof for cube {index}\n0\n".encode("ascii")


def digest_style(proof_digests: object, index: int) -> str:
    """Which of the three shapes cube ``index`` records its proof in.

    ``proof_digests`` is one style for the whole wave, or the collection of
    cubes that kept a proof - the rest then omit the pair, which is the mixed
    wave a campaign interrupted and resumed in ``--no-proof`` mode leaves.
    """
    if isinstance(proof_digests, str):
        if proof_digests not in DIGEST_STYLES:
            raise ValueError(f"unknown digest style {proof_digests!r}; expected {DIGEST_STYLES}")
        return proof_digests
    return DIGESTS_REAL if index in proof_digests else DIGESTS_ABSENT


def verdict_document(index: int, proof_digests: object = DIGESTS_REAL) -> dict:
    """One cube's verdict, in whichever of the three proof shapes it kept.

    A wave solved without proofs has no digest to record. Leaving the pair out
    is what the driver writes and what this repository reads as "no proof on
    record"; nulls are the older way of saying the same thing. Either way the
    wave can be honest evidence, and either way what it cannot be is
    drat-verified.
    """
    proof = placeholder_proof(index)
    document = {
        "cube": index,
        "lits": cube_literals(SPLIT, index),
        "rc": 20,
        "wall_s": 0.01,
    }
    style = digest_style(proof_digests, index)
    if style == DIGESTS_REAL:
        document["drat_sha256"] = hashlib.sha256(proof).hexdigest()
        document["drat_bytes"] = len(proof)
    elif style == DIGESTS_NULL:
        document["drat_sha256"] = None
        document["drat_bytes"] = None
    return document


def write_jsonl(path: Path, documents: list[dict], sort_keys: bool = True) -> None:
    """One JSON object per line, ASCII, LF, one trailing newline.

    ``sort_keys`` is a knob because nothing outside this repository promises to
    sort anything: a campaign's own writer emits whatever key order it built,
    and a fixture that always sorts cannot show that the import tool is what
    makes the committed file canonical.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(document, sort_keys=sort_keys) + "\n" for document in documents)
    path.write_bytes(body.encode("ascii"))


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="ascii").splitlines() if line.strip()
    ]


def write_wave(
    root: Path,
    directory: str,
    encoder: str,
    symmetry_break: bool,
    with_transcripts: bool,
    verdicts_form: str = "dir",
    proof_digests: object = DIGESTS_REAL,
) -> None:
    """Write one complete, honest wave under ``directory``.

    ``verdicts_form`` is ``"dir"`` for one JSON file per cube or ``"jsonl"``
    for the consolidated file a wave of thousands of cubes is committed as.
    Both carry the same verdict objects, and the gate has to hold every W rule
    identically over either. ``proof_digests`` is what each cube recorded about
    its proof - see ``digest_style``.
    """
    if verdicts_form not in VERDICT_FORMS:
        raise ValueError(
            f"unknown verdicts form {verdicts_form!r}; expected one of {VERDICT_FORMS}"
        )
    refute_every_cube(encoder, symmetry_break)

    write_json(root / directory / "manifest.json", manifest_document(encoder, symmetry_break))

    lines = []
    verdicts = [verdict_document(index, proof_digests) for index in range(N_CUBES)]
    if verdicts_form == "jsonl":
        write_jsonl(root / directory / "verdicts.jsonl", verdicts)
    for index in range(N_CUBES):
        proof = placeholder_proof(index)
        proof_sha = verdicts[index].get("drat_sha256")
        if verdicts_form == "dir":
            write_json(root / directory / "verdicts" / f"cube{index:04d}.json", verdicts[index])
        if not with_transcripts:
            continue
        proof_rel = f"{directory}/proofs/cube{index:04d}.drat.gz"
        proof_path = root / proof_rel
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        # mtime=0 so the fixture is byte-identical from one run to the next.
        with gzip.GzipFile(filename="", mode="wb", fileobj=proof_path.open("wb"), mtime=0) as out:
            out.write(proof)
        # Both proof fields are copied from the verdict, including when the
        # verdict kept none: a checker wrapper run over a proofless wave has
        # nothing else to write there, and a fixture that put a real byte count
        # beside an empty digest would fail W4 on the size and prove nothing
        # about the rule that a transcript's hash has to *be* a hash.
        lines.append(
            {
                "cube": index,
                "drat_sha256": proof_sha,
                "drat_bytes": verdicts[index].get("drat_bytes"),
                "proof_path_rel": proof_rel,
                "checker": "fixture placeholder - no checker ran",
                "verdict": "s VERIFIED",
            }
        )

    if with_transcripts:
        write_jsonl(root / directory / "transcripts.jsonl", lines)


# --- the off-repo shape a running campaign writes ---------------------------
#
# A live wave is cut and solved outside the repository: one verdict file per
# cube under `verdicts/`, one gzipped proof per cube under `drat/`, and a
# checker's own transcript line per proof - a different record from the one a
# wave is committed as, because it is written by the checker rather than for
# the gate. tools/import_wave.py is what turns one into the other, so the
# fixture for it has to be the shape the campaign actually produces.

SOURCE_TOOL = "fixture placeholder - no checker ran"


def cube_instance_sha(encoder: str, symmetry_break: bool, index: int) -> str:
    """The sha256 of one cube instance, as the checker fed it recorded it."""
    module = ENCODERS[encoder]
    n_vars, clauses = module.build(N, K, L, symmetry_break=symmetry_break)
    with tempfile.TemporaryDirectory(prefix="wavefix-") as work:
        base = write_cnf(Path(work) / "base.cnf", n_vars, clauses)
        info = write_cube_cnf(base["path"], SPLIT, index, Path(work) / "cube.cnf")
    return str(info["sha256"])


def reverse_keys(document: dict) -> dict:
    """The same object with its keys in the opposite order.

    JSON objects are unordered, so this changes nothing about what a line
    *means* - which is exactly why a tool that wants a byte-identical import
    twice has to impose an order of its own rather than inherit one.
    """
    return {key: document[key] for key in reversed(list(document))}


def write_source_wave(
    source: Path,
    encoder: str = "seqcount",
    symmetry_break: bool = True,
    *,
    with_transcripts: bool = True,
    with_proofs: bool = True,
    proof_digests: object = DIGESTS_REAL,
    verdicts_form: str = "dir",
    cube_order: list[int] | None = None,
    sort_keys: bool = True,
    transcript_extras: dict | None = None,
) -> Path:
    """Write a complete off-repo wave in the layout a campaign leaves behind.

    The defaults are the layout a fresh campaign writes: one verdict file per
    cube, one gzipped proof per cube, digests recorded for both. The knobs are
    the shapes a real campaign also produces, each of which the import tool has
    to handle correctly rather than by luck:

    * ``verdicts_form="jsonl"`` - the verdicts already consolidated, which is
      what a resumed campaign appends to instead of writing thousands of files.
    * ``cube_order`` - the order lines were appended, which is the order cubes
      *finished* and not cube order.
    * ``sort_keys=False`` - keys in the writer's own order (here, reversed),
      because nothing off-repo promises to sort them. It applies to the
      ``.jsonl`` writers; the per-cube files are pretty-printed and sorted.
    * ``proof_digests`` - a verdict-only wave that kept no proof has no digest
      to record: ``DIGESTS_ABSENT`` leaves the pair out, ``DIGESTS_NULL`` writes
      it empty, and a collection of cube ids is the mixed wave a campaign
      interrupted and resumed in the other mode leaves behind.
    * ``transcript_extras`` - keys the checker writes for itself beside the ones
      the import tool reads. The live checker records ``proof_pruned``, because
      it deletes each ``.drat.gz`` once it has read it, and a tool that had
      never heard of that key refused the whole file.
    """
    if verdicts_form not in VERDICT_FORMS:
        raise ValueError(
            f"unknown verdicts form {verdicts_form!r}; expected one of {VERDICT_FORMS}"
        )
    order = list(range(N_CUBES)) if cube_order is None else list(cube_order)
    if sorted(order) != list(range(N_CUBES)):
        raise ValueError(f"cube_order {order} is not a permutation of 0..{N_CUBES - 1}")
    refute_every_cube(encoder, symmetry_break)
    write_json(source / "manifest.json", manifest_document(encoder, symmetry_break))

    transcripts = []
    verdicts = []
    for index in order:
        proof = placeholder_proof(index)
        verdict = verdict_document(index, proof_digests)
        if verdicts_form == "jsonl":
            verdicts.append(verdict if sort_keys else reverse_keys(verdict))
        else:
            write_json(source / "verdicts" / f"v{index:05d}.json", verdict)
        if with_proofs:
            # Tens of gigabytes in the live case, and never committed: the
            # import tool has to leave every one of these behind.
            proof_path = source / "drat" / f"cube_{index:05d}.drat.gz"
            proof_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=proof_path.open("wb"), mtime=0
            ) as out:
                out.write(proof)
        transcripts.append(
            {
                "cube": index,
                "ok": True,
                "tool": SOURCE_TOOL,
                "tool_rc": 0,
                "verdict": "s VERIFIED",
                "drat_sha256": verdict.get("drat_sha256"),
                "drat_bytes": verdict.get("drat_bytes"),
                "cnf_sha256": cube_instance_sha(encoder, symmetry_break, index),
                "check_wall_s": 0.02,
                **(transcript_extras or {}),
            }
        )
    if verdicts_form == "jsonl":
        write_jsonl(source / "verdicts.jsonl", verdicts, sort_keys)
    if with_transcripts:
        write_jsonl(source / "transcripts.jsonl", transcripts, sort_keys)
    return source


def default_level(confirm: str | None, transcripts: bool) -> str:
    if confirm and transcripts:
        return "drat-transcript"
    if confirm:
        return "unsat-dual"
    if transcripts:
        return "wave-drat-verified"
    return "unsat-wave"


def build_wave_repo(
    root: Path,
    *,
    kind: str = "exact",
    confirm: str | None = "wave",
    transcripts: bool = True,
    evidence_level: str | None = None,
    verdicts_form: str = "dir",
    proof_digests: object = DIGESTS_REAL,
) -> Path:
    """Write a miniature repository whose single claim rests on a cube wave.

    ``confirm`` is ``"wave"`` for a second complete wave from a different
    encoder, ``"unsat_runs"`` for a monolithic run-log from a different encoder,
    or ``None`` for no confirmation at all. ``verdicts_form`` selects the shape
    both waves' verdicts are written in; the claim points at whichever it is.
    ``proof_digests`` applies to the primary wave, whose cubes may have been
    solved with proofs, without them, or - a campaign resumed in the other mode
    - some of each.
    """
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(GOOD / "claims" / "ANCHORS.json", _make(root / "claims" / "ANCHORS.json"))

    witness_block = None
    if kind == "exact":
        # write_witness refuses a coloring that does not avoid (k,l), so the
        # lower side of an exact claim is checked before it is written, twice:
        # here, and again by the gate.
        coloring = [1 if c == "+" else -1 for c in "--++--++"]
        witness_path = root / "evidence" / "witnesses" / f"k{K}_l{L}_N{N - 1}.txt"
        write_witness(witness_path, coloring, K, L, comments=("Gate fixture.",))
        witness_block = {
            "path": witness_path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(witness_path.read_bytes()).hexdigest(),
        }

    write_wave(root, PRIMARY_DIR, "seqcount", True, transcripts, verdicts_form, proof_digests)

    runs: list[str] = []
    confirm_block: dict | None = None
    if confirm == "wave":
        write_wave(root, CONFIRM_DIR, "totalizer", False, False, verdicts_form)
        confirm_block = {
            "kind": "wave",
            "manifest": CONFIRM_MANIFEST,
            "verdicts_dir": (
                CONFIRM_VERDICTS_JSONL if verdicts_form == "jsonl" else CONFIRM_VERDICTS
            ),
            "transcripts": None,
        }
    elif confirm == "unsat_runs":
        # A genuine kissat run-log from the committed good fixture: a different
        # encoder, the same (N,k,l), and an instance that regenerates.
        rel = "evidence/runs/k3_l2_N9_subsets.json"
        shutil.copyfile(GOOD / rel, _make(root / rel))
        runs.append(rel)
        confirm_block = {"kind": "unsat_runs"}

    claim = {
        "id": "N3_2_wave_9",
        "k": K,
        "l": L,
        "kind": kind,
        "value": N,
        "witness": witness_block,
        "unsat_runs": runs,
        "drat": None,
        "wave": {
            "manifest": MANIFEST,
            "verdicts_dir": VERDICTS_JSONL if verdicts_form == "jsonl" else VERDICTS,
            "transcripts": TRANSCRIPTS if transcripts else None,
            "confirm": confirm_block,
        },
        "evidence_level": evidence_level or default_level(confirm, transcripts),
        "prior_art": "a(3) of OEIS A398541.",
        "notes": "Gate fixture. Every cube here was refuted by tests/_minisolve.py.",
    }
    write_json(root / "claims" / "CLAIMS.json", {"schema": "nk2.claims.v1", "claims": [claim]})
    return root


def _make(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def patch_claim(root: Path, mutate) -> None:
    path = root / "claims" / "CLAIMS.json"
    document = read_json(path)
    mutate(document["claims"][0])
    write_json(path, document)


def patch_manifest(root: Path, mutate, directory: str = PRIMARY_DIR) -> None:
    path = root / directory / "manifest.json"
    manifest = read_json(path)
    mutate(manifest)
    write_json(path, manifest)


def patch_verdict(root: Path, index: int, mutate, directory: str = PRIMARY_DIR) -> None:
    """Break one cube's verdict, in whichever form this wave was written in."""
    consolidated = root / directory / "verdicts.jsonl"
    if consolidated.is_file():
        patch_verdict_line(root, index, mutate, directory)
        return
    path = root / directory / "verdicts" / f"cube{index:04d}.json"
    verdict = read_json(path)
    mutate(verdict)
    write_json(path, verdict)


def verdict_lines(root: Path, directory: str = PRIMARY_DIR) -> list[dict]:
    return read_jsonl(root / directory / "verdicts.jsonl")


def write_verdict_lines(root: Path, lines: list[dict], directory: str = PRIMARY_DIR) -> None:
    write_jsonl(root / directory / "verdicts.jsonl", lines)


def patch_verdict_line(root: Path, index: int, mutate, directory: str = PRIMARY_DIR) -> None:
    lines = verdict_lines(root, directory)
    mutate(next(line for line in lines if line["cube"] == index))
    write_verdict_lines(root, lines, directory)


def patch_source_manifest(source: Path, mutate) -> None:
    """Break the manifest of an off-repo wave, in place, before it is imported."""
    manifest = read_json(source / "manifest.json")
    mutate(manifest)
    write_json(source / "manifest.json", manifest)


def patch_source_verdict(source: Path, index: int, mutate) -> None:
    path = source / "verdicts" / f"v{index:05d}.json"
    verdict = read_json(path)
    mutate(verdict)
    write_json(path, verdict)


def patch_transcript(root: Path, index: int, mutate) -> None:
    path = root / TRANSCRIPTS
    lines = read_jsonl(path)
    mutate(lines[index])
    write_jsonl(path, lines)
