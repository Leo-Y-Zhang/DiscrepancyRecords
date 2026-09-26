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

from gate import verify_all
from gate.verify_all import ANCHOR_TERMS, LEVELS, main, regenerate
from nk2 import encode_subsets
from nk2.dimacs import write_cnf
from tests.test_gate_waves import stub_checker

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
    # scratch/ is the campaign's working tree, not evidence, and much of it is
    # gitignored, so this witness may be on one machine and in no checkout. No
    # `..` is needed to leave the evidence tree.
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


# --- --reverify-drat on a monolithic proof -----------------------------------
#
# drat-trim is absent from CI and application-control blocked on the development
# machine, so a stub checker stands in for it, as it does for waves. What these
# pin does not need a real checker: what the gate hands the checker, and whether
# it believes the answer.

TRANSCRIPT = "evidence/transcripts/k3_l2_N9_subsets.json"
PROOF = "evidence/drat/k3_l2_N9_subsets.drat"


def reverify_setup(tmp_path: Path, monkeypatch, instance: bytes, verdict_line: str) -> Path:
    """The good fixture with a DRAT block whose proof is on disk, ``instance``
    at the path the transcript names, and a stub checker that prints
    ``verdict_line`` whatever it is given."""
    root = copy_good(tmp_path)
    add_verified_drat(root, TRANSCRIPT, PROOF)
    (root / PROOF).parent.mkdir(parents=True, exist_ok=True)
    (root / PROOF).write_bytes(b"0\n")
    instance_rel = json.loads((root / TRANSCRIPT).read_text(encoding="ascii"))["instance_path_rel"]
    (root / instance_rel).parent.mkdir(parents=True, exist_ok=True)
    (root / instance_rel).write_bytes(instance)
    checker = stub_checker(tmp_path / "bin", verdict_line)
    monkeypatch.setattr(verify_all, "find_drat_trim", lambda: checker)
    return root


def regenerated_instance(tmp_path: Path) -> bytes:
    n_vars, clauses = encode_subsets.build(9, 3, 2)
    return Path(write_cnf(tmp_path / "regenerated.cnf", n_vars, clauses)["path"]).read_bytes()


def test_reverify_reaches_drat_reverified_on_the_regenerated_instance(
    capsys, tmp_path, monkeypatch
):
    root = reverify_setup(tmp_path, monkeypatch, regenerated_instance(tmp_path), "s VERIFIED")
    code, out = run(root, capsys, extra=["--reverify-drat"])
    assert code == 0, out
    assert "declares 'drat-transcript', reaches 'drat-reverified'" in out


def test_reverify_refuses_a_run_proof_the_checker_rejects(capsys, tmp_path, monkeypatch):
    root = reverify_setup(
        tmp_path, monkeypatch, regenerated_instance(tmp_path), "s NOT VERIFIED"
    )
    code, out = run(root, capsys, extra=["--reverify-drat"])
    assert code != 0, out
    assert "FAIL G4 N3_2_exact_9 drat-trim re-run did not verify" in out


def test_reverify_refuses_an_instance_g3_did_not_regenerate(capsys, tmp_path, monkeypatch):
    # The instance is gitignored bulk, so the file at the recorded path is
    # whatever is on disk. Two complementary units are unsatisfiable by unit
    # propagation, and real drat-trim verifies the one-line proof "0" against
    # them - so a checker's VERIFIED is only about N = 9 if the file is N = 9.
    root = reverify_setup(tmp_path, monkeypatch, b"p cnf 1 2\n1 0\n-1 0\n", "s VERIFIED")
    code, out = run(root, capsys, extra=["--reverify-drat"])
    assert code != 0, out
    assert "on disk is not the instance G3 regenerated" in out


# --- checks no fixture above exercises ----------------------------------------
#
# Each test below was found by deleting one check from the gate and watching the
# whole suite, and the bare gate, stay green. Several of the cases also break a
# second rule, so each asserts the rule *and* its reason: with the check under
# test deleted, the gate would still exit non-zero, for the other rule.

def edit_json(path: Path, mutate) -> None:
    document = json.loads(path.read_text(encoding="ascii"))
    mutate(document)
    write_json(path, document)


def test_witness_substituted_for_another_valid_one_is_refused(capsys, tmp_path):
    # The negated coloring avoids (3,2) just as well - negation leaves every AP
    # sum's absolute value alone - so re-evaluating it cannot tell the two
    # apart. The claim records which witness it rests on, and the sha256 is
    # what holds it to that one.
    root = copy_good(tmp_path)
    path = root / "evidence" / "witnesses" / "k3_l2_N8.txt"
    lines = path.read_bytes().split(b"\n")
    data = next(i for i, line in enumerate(lines) if line and not line.startswith(b"#"))
    lines[data] = lines[data].translate(bytes.maketrans(b"+-", b"-+"))
    path.write_bytes(b"\n".join(lines))
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G2 N3_2_exact_9 witness sha256 mismatch" in out


@pytest.mark.parametrize(("field", "value"), [("N", 10), ("k", 4), ("l", 3)])
def test_run_logs_about_another_instance_are_refused(field, value, capsys, tmp_path):
    # Both run-logs are rewritten to describe, consistently, a different
    # instance: its parameters, sha256 and counts all regenerate, and each still
    # says UNSAT with rc 20. UNSAT at N = 10 says nothing about N = 9, so the only
    # thing wrong is that the solving answered another question.
    def retarget(log):
        instance = log["instance"]
        instance[field] = value
        sha, n_vars, n_clauses = regenerate(
            instance["N"], instance["k"], instance["l"], instance["encoder"],
            instance["symmetry_break"],
        )
        instance.update({"sha256": sha, "n_vars": n_vars, "n_clauses": n_clauses})

    root = copy_good(tmp_path)
    for log_path in sorted((root / "evidence" / "runs").glob("*.json")):
        edit_json(log_path, retarget)
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G3" in out and "the claim needs (9, 3, 2)" in out


def test_run_log_whose_verdict_is_not_unsat_is_refused_at_rc_20(capsys, tmp_path):
    # G3 wants both: verdict UNSAT and rc 20. The g3 fixture pins the rc; this
    # pins the verdict. solve.py derives one from the other, so a log where they
    # disagree has been edited or truncated since it was written.
    root = copy_good(tmp_path)
    edit_json(
        root / "evidence" / "runs" / "k3_l2_N9_subsets.json",
        lambda log: log.update({"verdict": "UNKNOWN"}),
    )
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G3" in out and "is verdict 'UNKNOWN' with rc 20" in out


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("proof_sha256", "0" * 64, "transcript proof sha256 does not match the claim"),
        ("proof_bytes", 3, "transcript proof byte count does not match the claim"),
        (
            "instance_sha256",
            regenerate(10, 3, 2, "subsets", False)[0],
            "transcript instance sha256 is not one of the instances verified by G3",
        ),
    ],
    ids=["proof_sha256", "proof_bytes", "instance_sha256"],
)
def test_transcript_about_another_proof_or_instance_is_refused(
    key, value, reason, capsys, tmp_path
):
    # The transcript still ends 's VERIFIED'. It is a checker's verdict on some
    # other proof, or on a proof of some other instance (here, a genuine
    # instance at N = 10), and so it says nothing about this claim.
    root = copy_good(tmp_path)
    add_verified_drat(root, TRANSCRIPT, PROOF)
    edit_json(root / TRANSCRIPT, lambda transcript: transcript.update({key: value}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert f"FAIL G4 N3_2_exact_9 {reason}" in out


def test_proof_on_disk_that_is_not_the_recorded_one_is_refused(capsys, tmp_path):
    # An absent proof is allowed: it only means nothing can re-run the checker.
    # A proof that is present has to be the one the claim and transcript name.
    root = copy_good(tmp_path)
    add_verified_drat(root, TRANSCRIPT, PROOF)
    (root / PROOF).parent.mkdir(parents=True, exist_ok=True)
    (root / PROOF).write_bytes(b"1\n")  # the recorded proof is b"0\n": same size
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G4" in out and "proof on disk does not match the recorded sha256" in out


def test_proof_on_disk_shorter_than_its_recorded_byte_count_is_refused(capsys, tmp_path):
    # The claim and the transcript agree on the proof's sha256 and on a byte
    # count, and the proof on disk is the one that sha256 names. The byte count
    # is not its length, so the record contradicts itself, and only the size
    # check on the proof itself can see it: the transcript check compares two
    # copies of the same wrong number.
    root = copy_good(tmp_path)
    add_verified_drat(root, TRANSCRIPT, PROOF)
    edit_json(root / TRANSCRIPT, lambda transcript: transcript.update({"proof_bytes": 3}))
    patch_claim(root, lambda claim: claim["drat"].update({"proof_bytes": 3}))
    (root / PROOF).parent.mkdir(parents=True, exist_ok=True)
    (root / PROOF).write_bytes(b"0\n")  # the recorded proof, two bytes long
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G4 N3_2_exact_9 proof on disk does not match the recorded byte count" in out


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"value": 10}, "exact 10 contradicts published a(3) = 9"),
        ({"kind": "lower_bound", "value": 10}, "lower bound 10 exceeds published a(3) = 9"),
        ({"kind": "upper_bound", "value": 8}, "upper bound 8 is below published a(3) = 9"),
        # The g5 fixture is k = 19; k = 18 is the first term that is not contiguous.
        ({"k": 18}, "exact a(18) is not contiguous with a(16); a(17) is still open"),
    ],
    ids=["exact", "lower_bound", "upper_bound", "k18"],
)
def test_claim_that_disagrees_with_the_published_terms_is_refused(
    patch, reason, capsys, tmp_path
):
    root = copy_good(tmp_path)
    patch_claim(root, lambda claim: claim.update(patch))
    code, out = run(root, capsys)
    assert code != 0, out
    assert f"FAIL G5 N3_2_exact_9 {reason}" in out


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("sequence", "A000001", "ANCHORS.json is for 'A000001', not A398541"),
        ("offset", 1, "ANCHORS.json offset is 1, not 2"),
        (
            "terms",
            [*ANCHOR_TERMS[:-1], ANCHOR_TERMS[-1] + 1],
            "ANCHORS.json terms disagree with the copy held in the gate",
        ),
    ],
    ids=["sequence", "offset", "terms"],
)
def test_anchor_file_that_disagrees_with_the_gate_is_refused(key, value, reason, capsys, tmp_path):
    # The gate holds its own copy of the published terms so that an edited
    # ANCHORS.json cannot quietly redefine what "consistent with the
    # literature" means. The claim itself is untouched and still verifies.
    root = copy_good(tmp_path)
    edit_json(root / "claims" / "ANCHORS.json", lambda anchors: anchors.update({key: value}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert f"FAIL G5 - {reason}" in out


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("N(17,2) \u2265 274\n".encode(), "non-ASCII byte at offset 8"),
        # Split so that this file does not itself hold what G6 looks for.
        (b"solved under /home" b"/someone/sat\n", "absolute path '/home" "/s'"),
        (b"solved under /Users" b"/someone/sat\n", "absolute path '/Users" "/s'"),
    ],
    ids=["non-ascii", "home", "users"],
)
def test_committed_text_that_g6_refuses(content, reason, capsys, tmp_path):
    # The g6 fixtures cover drive letters. These are the other shapes G6 names.
    root = copy_good(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "note.md").write_bytes(content)
    code, out = run(root, capsys)
    assert code != 0, out
    assert f"FAIL G6 docs/note.md {reason}" in out


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"k": 1}, "k must be at least 2"),
        ({"evidence_level": "proved"}, "unknown evidence_level 'proved'"),
        ({"notes": None}, "notes must be a string"),
    ],
    ids=["k1", "evidence_level", "notes"],
)
def test_claim_field_of_the_wrong_shape_is_refused(patch, reason, capsys, tmp_path):
    # G1 refuses these before any rule reads them. Without it, k = 1 and an
    # unknown level each crash the gate with a traceback further on, and notes
    # that are not text are accepted without a word.
    root = copy_good(tmp_path)
    patch_claim(root, lambda claim: claim.update(patch))
    code, out = run(root, capsys)
    assert code != 0, out
    assert f"FAIL G1 N3_2_exact_9 {reason}" in out


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda document: document.update({"schema": "nk2.claims.v2"}),
            "CLAIMS.json must have exactly schema and claims",
        ),
        (
            lambda document: document.update({"retracted": []}),
            "CLAIMS.json must have exactly schema and claims",
        ),
        (
            lambda document: document.update({"claims": {"N3_2_exact_9": document["claims"][0]}}),
            "claims must be a list",
        ),
        (
            lambda document: document["claims"].append("N(3,2) = 9"),
            "every entry of claims must be an object",
        ),
    ],
    ids=["schema", "extra-key", "claims-not-a-list", "claim-not-an-object"],
)
def test_claims_file_with_the_wrong_envelope_is_refused(mutate, reason, capsys, tmp_path):
    # The claim inside is untouched and verifies on its own; only the file
    # around it is wrong, and a reader of another schema version, or one that
    # skips what it cannot read, would take it to say something else.
    root = copy_good(tmp_path)
    edit_json(root / "claims" / "CLAIMS.json", mutate)
    code, out = run(root, capsys)
    assert code != 0, out
    assert f"FAIL G1 - {reason}" in out


def test_two_claims_with_one_id_are_refused(capsys, tmp_path):
    # Each copy verifies on its own. An id is how a claim is cited, so two
    # records under one id is refused whether or not both are true.
    root = copy_good(tmp_path)
    edit_json(
        root / "claims" / "CLAIMS.json",
        lambda document: document["claims"].append(dict(document["claims"][0])),
    )
    code, out = run(root, capsys)
    assert code != 0, out
    assert "FAIL G1 N3_2_exact_9 duplicate claim id" in out


def test_gate_fails_on_its_own_when_the_evaluator_accepts_everything(capsys, monkeypatch):
    # With avoids() gutted, no committed claim looks any different, because none
    # of them is wrong - G2 has nothing to catch. The self-check is the only
    # thing that can see it, and it has to turn the exit code red by itself.
    monkeypatch.setattr(verify_all, "avoids", lambda f, k, l: True)
    code, out = run(FIXTURES / "good", capsys)
    assert code != 0, out
    assert [line.split()[:2] for line in out.splitlines() if line.startswith("FAIL ")] == [
        ["FAIL", "SELFTEST"]
    ]
