"""The gate is the only thing that speaks for this repository, so its refusals
are tested one rule at a time.

Each fixture under tests/fixtures/ is a miniature repository with exactly one
thing wrong. The gate must exit non-zero *and* name the rule: an exit code alone
would pass even if the gate failed for an unrelated reason.
"""

import ast
import json
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
    assert "OK 2 claim(s)" in out


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
    assert LEVELS == ("witness", "unsat-dual", "drat-transcript", "drat-reverified")


def test_gate_anchor_literal_is_the_published_data():
    assert ANCHOR_TERMS == (3, 9, 13, 22, 11, 49, 57, 65, 19, 112, 45, 158, 27, 225, 241)
    assert len(ANCHOR_TERMS) == 15


def test_missing_claims_file_fails(capsys, tmp_path):
    (tmp_path / "claims").mkdir()
    code, out = run(tmp_path, capsys)
    assert code != 0
    assert "FAIL G5" in out and "FAIL G1" in out
