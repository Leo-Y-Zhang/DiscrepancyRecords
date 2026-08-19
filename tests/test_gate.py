"""The gate is the only thing that speaks for this repository, so its refusals
are tested one rule at a time.

Each fixture under tests/fixtures/ is a miniature repository with exactly one
thing wrong. The gate must exit non-zero *and* name the rule: an exit code alone
would pass even if the gate failed for an unrelated reason.
"""

import ast
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from gate.verify_all import ANCHOR_TERMS, LEVELS, main

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

BAD = [
    ("g1_unknown_key", "G1"),
    ("g1_unknown_kind", "G1"),
    ("g2_flipped_sign", "G2"),
    ("g2_wrong_length", "G2"),
    ("g2_missing_witness", "G2"),
    ("g3_single_encoder", "G3"),
    ("g3_verdict_without_rc", "G3"),
    ("g3_sha_mismatch", "G3"),
    ("g4_transcript_not_verified", "G4"),
    ("g5_noncontiguous_k19", "G5"),
    ("g6_absolute_path", "G6"),
    ("g6_absolute_path_escaped", "G6"),
    ("g7_overstated_level", "G7"),
]


def run(root: Path, capsys, extra=()):
    code = main(["--root", str(root), *extra])
    return code, capsys.readouterr().out


def copy_good(tmp_path: Path) -> Path:
    """A private copy of the good fixture, so a test may break it freely."""
    root = tmp_path / "repo"
    shutil.copytree(FIXTURES / "good", root)
    return root


def write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(document, indent=2, sort_keys=True) + "\n").encode("ascii"))


def patch_claim(root: Path, mutate) -> None:
    """Apply ``mutate`` to the single claim of a fixture copy and write it back."""
    path = root / "claims" / "CLAIMS.json"
    document = json.loads(path.read_text(encoding="ascii"))
    mutate(document["claims"][0])
    write_json(path, document)


def move_out(root: Path, rel_path: str, destination: Path) -> Path:
    """Move an artifact somewhere else and return where it landed."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(root / rel_path), str(destination))
    return destination


def add_verified_drat(root: Path, transcript_rel: str, proof_rel: str) -> None:
    """Give the good fixture a DRAT block that satisfies G4 on its own terms.

    Nothing here needs drat-trim: G4 checks the transcript against the claim and
    against the instance G3 regenerated, and a proof that is not on disk is
    allowed. That is what makes it usable as a positive control.
    """
    log = json.loads((root / "evidence/runs/k3_l2_N9_subsets.json").read_text(encoding="ascii"))
    proof = b"0\n"
    write_json(
        root / transcript_rel,
        {
            "schema": "nk2.transcript.v1",
            "tool": "drat-trim",
            "rc": 0,
            "instance_path_rel": log["instance"]["path_rel"],
            "instance_sha256": log["instance"]["sha256"],
            "proof_path_rel": proof_rel,
            "proof_sha256": hashlib.sha256(proof).hexdigest(),
            "proof_bytes": len(proof),
            "output_tail": ["c parsing input file", "s VERIFIED"],
        },
    )
    patch_claim(
        root,
        lambda claim: claim.update(
            {
                "drat": {
                    "proof_sha256": hashlib.sha256(proof).hexdigest(),
                    "proof_bytes": len(proof),
                    "transcript": transcript_rel,
                },
                "evidence_level": "drat-transcript",
            }
        ),
    )


def test_every_fixture_directory_is_covered():
    # A fixture nobody runs is not a test. Keep the list and the tree in step.
    on_disk = {p.name for p in FIXTURES.iterdir() if p.is_dir()}
    assert on_disk == {name for name, _ in BAD} | {"good"}


def test_good_fixture_passes(capsys):
    code, out = run(FIXTURES / "good", capsys)
    assert code == 0, out
    assert "FAIL" not in out


@pytest.mark.parametrize(("name", "rule"), BAD, ids=[n for n, _ in BAD])
def test_bad_fixture_is_refused(name, rule, capsys):
    code, out = run(FIXTURES / name, capsys)
    assert code != 0, out
    failures = [line for line in out.splitlines() if line.startswith("FAIL ")]
    assert failures, out
    assert any(line.startswith(f"FAIL {rule} ") for line in failures), out


def test_real_claims_verify(capsys):
    code, out = run(ROOT, capsys)
    assert code == 0, out
    # The gate must have verified every claim in the committed claims file,
    # not a subset - pin the count to the file so a skipped claim cannot hide.
    n = len(json.loads((ROOT / "claims" / "CLAIMS.json").read_text())["claims"])
    assert n >= 2
    assert f"OK {n} claim(s)" in out


def test_gate_needs_no_solver():
    # The gate must be cold-runnable on a machine with nothing installed. The
    # check is structural: it never imports the subprocess driver at all.
    tree = ast.parse((ROOT / "gate" / "verify_all.py").read_text(encoding="ascii"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "nk2.solve" not in imported
    assert not any(name.startswith("pysat") for name in imported)


def test_gate_regenerates_rather_than_trusting_the_recorded_hash(capsys, tmp_path):
    # M13: if the gate skipped regeneration, corrupting the instance hash in a
    # run-log would go unnoticed. Do it to a copy of the good fixture here so the
    # coverage does not depend on the committed fixture staying corrupted.
    import shutil

    root = tmp_path / "repo"
    shutil.copytree(FIXTURES / "good", root)
    log_path = root / "evidence" / "runs" / "k3_l2_N9_seqcount.json"
    log = json.loads(log_path.read_text(encoding="ascii"))
    log["instance"]["n_clauses"] = log["instance"]["n_clauses"] + 1
    log_path.write_bytes(
        (json.dumps(log, indent=2, sort_keys=True) + "\n").encode("ascii")
    )
    code, out = run(root, capsys)
    assert code != 0
    assert "FAIL G3" in out


def test_unreferenced_artifact_is_a_warning_not_a_failure(capsys, tmp_path):
    import shutil

    root = tmp_path / "repo"
    shutil.copytree(FIXTURES / "good", root)
    (root / "evidence" / "witnesses" / "orphan.txt").write_bytes(b"# orphan\n++--\n")
    code, out = run(root, capsys)
    assert code == 0, out
    assert "WARN artifact evidence/witnesses/orphan.txt" in out


def test_understated_evidence_is_info_not_failure(capsys, tmp_path):
    import shutil

    root = tmp_path / "repo"
    shutil.copytree(FIXTURES / "good", root)
    claims_path = root / "claims" / "CLAIMS.json"
    doc = json.loads(claims_path.read_text(encoding="ascii"))
    doc["claims"][0]["evidence_level"] = "witness"  # really reaches unsat-dual
    claims_path.write_bytes((json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("ascii"))
    code, out = run(root, capsys)
    assert code == 0, out
    assert "INFO" in out and "understates" in out


def test_levels_are_ordered_as_documented():
    assert LEVELS == (
        "witness",
        "unsat-wave",
        "wave-drat-verified",
        "unsat-dual",
        "drat-transcript",
        "drat-reverified",
    )


def test_gate_anchor_literal_is_the_published_data():
    assert ANCHOR_TERMS == (3, 9, 13, 22, 11, 49, 57, 65, 19, 112, 45, 158, 27, 225, 241)
    assert len(ANCHOR_TERMS) == 15


def test_missing_claims_file_fails(capsys, tmp_path):
    (tmp_path / "claims").mkdir()
    code, out = run(tmp_path, capsys)
    assert code != 0
    assert "FAIL G5" in out and "FAIL G1" in out


# --- artifact paths ---------------------------------------------------------
#
# A claim may only point at committed evidence of the repository being checked.
# Every test below leaves an artifact that is genuine, unmodified and readable -
# only its location is wrong - so a gate that merely joins the path to the root
# reports "verified from artifacts on disk" for a checkout that does not contain
# the evidence. That is the exact deception the gate exists to prevent, so each
# case is a failure, not a warning.


def test_witness_outside_the_root_is_refused(capsys, tmp_path):
    root = copy_good(tmp_path)
    move_out(root, "evidence/witnesses/k3_l2_N8.txt", tmp_path / "elsewhere/k3_l2_N8.txt")
    patch_claim(root, lambda claim: claim["witness"].update({"path": "../elsewhere/k3_l2_N8.txt"}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G2" in out and "climbs out of the repository" in out


def test_witness_in_a_gitignored_directory_is_refused(capsys, tmp_path):
    # scratch/ is gitignored, so this witness is on one machine and in no
    # checkout. No `..` is needed to leave the evidence tree.
    root = copy_good(tmp_path)
    move_out(root, "evidence/witnesses/k3_l2_N8.txt", root / "scratch/k3_l2_N8.txt")
    patch_claim(root, lambda claim: claim["witness"].update({"path": "scratch/k3_l2_N8.txt"}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G2" in out and "is outside evidence/witnesses/" in out


def test_absolute_witness_path_is_refused(capsys, tmp_path):
    # The artifact is where it belongs; only the way the claim names it is
    # wrong. G6 also objects to a drive letter in a claims file, so this asserts
    # on G2 specifically - path containment must not depend on the text scan.
    root = copy_good(tmp_path)
    absolute = (root / "evidence/witnesses/k3_l2_N8.txt").resolve().as_posix()
    patch_claim(root, lambda claim: claim["witness"].update({"path": absolute}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G2" in out and "is not a plain repo-relative path" in out


def link_directory(link: Path, target: Path) -> None:
    """Point ``link`` at ``target``, or skip: a symlink needs a privilege on
    Windows that a junction does not, and one of the two is always available."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt":
        pytest.skip("no way to create a directory link here")
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0 or not link.exists():
        pytest.skip(f"no way to create a directory link here: {completed.stdout.strip()}")


def test_witness_linked_out_of_the_repository_is_refused(capsys, tmp_path):
    # Every string check passes: the claim names evidence/witnesses/... exactly
    # as it should. The directory it names is somebody else's.
    root = copy_good(tmp_path)
    witnesses = root / "evidence" / "witnesses"
    outside = tmp_path / "elsewhere"
    shutil.move(str(witnesses), str(outside))
    link_directory(witnesses, outside)
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G2" in out and "resolves outside the repository" in out


def test_non_canonical_witness_path_is_refused(capsys, tmp_path):
    # It names the right file. It is not the spelling the artifact is filed
    # under, so every cross-reference to it compares unequal strings.
    root = copy_good(tmp_path)
    patch_claim(
        root, lambda claim: claim["witness"].update({"path": "./evidence/witnesses/k3_l2_N8.txt"})
    )
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G2" in out and "is not a plain repo-relative path" in out


def test_witness_with_a_gitignored_suffix_is_refused(capsys, tmp_path):
    # .cnf and .drat are ignored wherever they sit under evidence/, so a
    # witness named like one is in the right directory and still in no checkout.
    root = copy_good(tmp_path)
    renamed = "evidence/witnesses/k3_l2_N8.cnf"
    move_out(root, "evidence/witnesses/k3_l2_N8.txt", root / renamed)
    patch_claim(root, lambda claim: claim["witness"].update({"path": renamed}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G2" in out and "names a gitignored bulk artifact" in out


def test_run_log_in_a_gitignored_directory_is_refused(capsys, tmp_path):
    root = copy_good(tmp_path)
    move_out(root, "evidence/runs/k3_l2_N9_subsets.json", root / "scratch/subsets.json")
    patch_claim(root, lambda claim: claim["unsat_runs"].__setitem__(0, "scratch/subsets.json"))
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G3" in out and "is outside evidence/runs/" in out


def test_run_log_outside_the_root_is_refused(capsys, tmp_path):
    root = copy_good(tmp_path)
    move_out(root, "evidence/runs/k3_l2_N9_subsets.json", tmp_path / "elsewhere/subsets.json")
    patch_claim(
        root, lambda claim: claim["unsat_runs"].__setitem__(0, "../elsewhere/subsets.json")
    )
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G3" in out and "climbs out of the repository" in out


def test_transcript_in_place_reaches_drat_transcript(capsys, tmp_path):
    # The positive control for the three tests around it: the same synthetic
    # DRAT block, in the directory it belongs in, must still pass.
    root = copy_good(tmp_path)
    add_verified_drat(
        root, "evidence/transcripts/k3_l2_N9_subsets.json", "evidence/drat/k3_l2_N9_subsets.drat"
    )
    code, out = run(root, capsys)
    assert code == 0, out
    assert "FAIL" not in out


def test_transcript_in_a_gitignored_directory_is_refused(capsys, tmp_path):
    root = copy_good(tmp_path)
    add_verified_drat(root, "scratch/transcript.json", "evidence/drat/k3_l2_N9_subsets.drat")
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G4" in out and "is outside evidence/transcripts/" in out


def test_proof_outside_the_root_is_refused(capsys, tmp_path):
    # The proof is real and its sha256 and byte count both match what the claim
    # records; it simply is not in this repository.
    root = copy_good(tmp_path)
    add_verified_drat(
        root, "evidence/transcripts/k3_l2_N9_subsets.json", "../elsewhere/proof.drat"
    )
    proof = tmp_path / "elsewhere" / "proof.drat"
    proof.parent.mkdir(parents=True, exist_ok=True)
    proof.write_bytes(b"0\n")
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G4" in out and "climbs out of the repository" in out


def test_transcript_instance_outside_the_evidence_tree_is_refused(capsys, tmp_path):
    root = copy_good(tmp_path)
    add_verified_drat(
        root, "evidence/transcripts/k3_l2_N9_subsets.json", "evidence/drat/k3_l2_N9_subsets.drat"
    )
    transcript = root / "evidence/transcripts/k3_l2_N9_subsets.json"
    document = json.loads(transcript.read_text(encoding="ascii"))
    document["instance_path_rel"] = "scratch/k3_l2_N9_subsets.cnf"
    write_json(transcript, document)
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G4" in out and "is outside evidence/" in out
