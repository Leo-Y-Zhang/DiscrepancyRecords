"""tools/import_wave.py turns a finished off-repo wave into committed evidence,
and the only thing standing between a half-finished campaign and a claim that
says "every cube came back UNSAT" is what it refuses.

So every test here is either the round trip - a source wave the fixture refuted
cube by cube, imported, then handed to the gate, which must exit 0 - or a
refusal: a wave that is incomplete, a cube that timed out, literals that are not
the construction's, a cube-set hash the split does not produce, or a checker
transcript that does not line up with the verdicts it claims to be about. Each
refusal has been observed failing against a copy of the tool with that one check
deleted; the runs are recorded in the session report rather than here, because a
test that has never been seen to fail proves nothing.

The tool must also leave the source alone (a live campaign is still writing to
it) and write nothing outside the wave directory it was asked for.
"""

import hashlib
import json
import re
import shutil

import pytest

from gate.verify_all import main as gate_main
from tests._wavefix import (
    GOOD,
    N_CUBES,
    N,
    patch_source_manifest,
    patch_source_verdict,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
    write_source_wave,
)
from tools import import_wave

NAME = "k3_l2_N9_seqcount"
WAVE_DIR = f"evidence/waves/{NAME}"


def make_repo(tmp_path, name="repo"):
    """A repository holding the anchors and no claim, ready to receive a wave."""
    root = tmp_path / name
    (root / "claims").mkdir(parents=True)
    shutil.copyfile(GOOD / "claims" / "ANCHORS.json", root / "claims" / "ANCHORS.json")
    write_json(root / "claims" / "CLAIMS.json", {"schema": "nk2.claims.v1", "claims": []})
    return root


def run_import(root, source, *extra):
    return import_wave.main(
        ["--source", str(source), "--name", NAME, "--root", str(root), *extra]
    )


def snapshot(directory):
    """Every file under ``directory``, by relative path and sha256."""
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def fragments(out):
    """The claim fragments the tool prints, parsed."""
    blocks = {}
    current = None
    body = []
    for line in out.splitlines():
        if line.startswith("--- claim fragment: ") and line.endswith(" ---"):
            current = line.removeprefix("--- claim fragment: ").removesuffix(" ---")
            body = []
        elif line.startswith("--- end ") and current is not None:
            blocks[current] = json.loads("\n".join(body))
            current = None
        elif current is not None:
            body.append(line)
    return blocks


# --- the round trip ---------------------------------------------------------


def test_imported_wave_passes_the_gate(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")

    assert run_import(root, source) == 0
    out = capsys.readouterr().out

    # What landed: a manifest, one consolidated verdicts file, one transcripts
    # file, and nothing else at all.
    written = sorted(p.relative_to(root).as_posix() for p in (root / WAVE_DIR).rglob("*"))
    assert written == [
        f"{WAVE_DIR}/manifest.json",
        f"{WAVE_DIR}/transcripts.jsonl",
        f"{WAVE_DIR}/verdicts.jsonl",
    ]

    # And the claim the tool printed verifies against it.
    claim = fragments(out)["this wave alone (upper_bound_wave)"]
    write_json(root / "claims" / "CLAIMS.json", {"schema": "nk2.claims.v1", "claims": [claim]})
    assert gate_main(["--root", str(root)]) == 0, capsys.readouterr().out


def test_the_exact_fragment_is_a_template_naming_a_confirming_wave(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    assert run_import(root, source) == 0
    claim = fragments(capsys.readouterr().out)["with a confirming wave (exact)"]
    assert claim["kind"] == "exact"
    assert claim["wave"]["confirm"]["kind"] == "wave"
    assert import_wave.CONFIRM_PLACEHOLDER in claim["wave"]["confirm"]["manifest"]


def test_verdicts_are_sorted_by_cube_with_a_stable_key_order(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    assert run_import(root, source) == 0
    capsys.readouterr()
    lines = (root / WAVE_DIR / "verdicts.jsonl").read_text(encoding="ascii").splitlines()
    assert [json.loads(line)["cube"] for line in lines] == list(range(N_CUBES))
    for line in lines:
        assert list(json.loads(line)) == sorted(json.loads(line))


def test_two_imports_of_one_source_are_byte_identical(capsys, tmp_path):
    source = write_source_wave(tmp_path / "source")
    first, second = make_repo(tmp_path, "one"), make_repo(tmp_path, "two")
    assert run_import(first, source) == 0
    assert run_import(second, source) == 0
    capsys.readouterr()
    assert snapshot(first / WAVE_DIR) == snapshot(second / WAVE_DIR)


def test_the_source_is_never_touched(capsys, tmp_path):
    # The campaign is still running against this directory; a wave in flight
    # must not be edited, moved or consolidated in place.
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    before = snapshot(source)
    assert run_import(root, source) == 0
    capsys.readouterr()
    assert snapshot(source) == before


def test_no_proof_is_ever_copied(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    assert list((source / "drat").glob("*.drat.gz"))
    assert run_import(root, source) == 0
    capsys.readouterr()
    assert not list(root.rglob("*.drat.gz"))
    assert not list(root.rglob("*.drat"))


def test_dry_run_writes_nothing(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    before = snapshot(root)
    assert run_import(root, source, "--dry-run") == 0
    out = capsys.readouterr().out
    assert snapshot(root) == before
    assert not (root / WAVE_DIR).exists()
    # It still says what it would have written, and still prints the claim.
    assert "DRY RUN" in out
    assert fragments(out)["this wave alone (upper_bound_wave)"]["value"] == N


def test_summary_reports_the_campaign_totals(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    patch_source_verdict(source, 2, lambda v: v.update({"wall_s": 12.5}))
    assert run_import(root, source, "--dry-run") == 0
    out = capsys.readouterr().out
    assert re.search(rf"cubes:\s+{N_CUBES}\b", out), out
    assert "12.5" in out  # the longest cube, and it is in the total below
    assert "seqcount" in out
    assert read_json(source / "manifest.json")["base"]["sha256"] in out


def test_a_wave_without_transcripts_imports_and_earns_less(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source", with_transcripts=False, with_proofs=False)
    assert run_import(root, source) == 0
    out = capsys.readouterr().out
    assert not (root / WAVE_DIR / "transcripts.jsonl").exists()
    claim = fragments(out)["this wave alone (upper_bound_wave)"]
    assert claim["wave"]["transcripts"] is None
    assert claim["evidence_level"] == "unsat-wave"

    write_json(root / "claims" / "CLAIMS.json", {"schema": "nk2.claims.v1", "claims": [claim]})
    assert gate_main(["--root", str(root)]) == 0, capsys.readouterr().out


# --- refusals ---------------------------------------------------------------


def test_an_incomplete_wave_is_refused_naming_the_missing_cubes(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    (source / "verdicts" / "v00002.json").unlink()
    assert run_import(root, source) == 1
    err = capsys.readouterr().err
    assert "cube 2" in err
    assert not (root / WAVE_DIR).exists()


def test_a_cube_with_a_null_return_code_is_refused(capsys, tmp_path):
    # The live shape of an unfinished cube: an external timeout killed the
    # solver and the verdict was written with rc null.
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    patch_source_verdict(source, 1, lambda v: v.update({"rc": None}))
    assert run_import(root, source) == 1
    err = capsys.readouterr().err
    assert "cube 1" in err and "rc" in err
    assert not (root / WAVE_DIR).exists()


def test_a_satisfiable_cube_is_refused(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    patch_source_verdict(source, 0, lambda v: v.update({"rc": 10}))
    assert run_import(root, source) == 1
    assert "cube 0" in capsys.readouterr().err


def test_tampered_literals_are_refused(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    patch_source_verdict(source, 3, lambda v: v.update({"lits": [2, -5]}))
    assert run_import(root, source) == 1
    err = capsys.readouterr().err
    assert "cube 3" in err and ("lits" in err or "literals" in err)


def test_a_cubes_hash_the_split_does_not_produce_is_refused(capsys, tmp_path):
    # The manifest is the one artifact a wave carries about completeness, and
    # the tool re-derives the cube set from split_vars exactly as the gate does
    # rather than believing the recorded digest.
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    patch_source_manifest(source, lambda m: m.update({"cubes_sha256": "f" * 64}))
    assert run_import(root, source) == 1
    assert "cubes_sha256" in capsys.readouterr().err


def test_a_cube_count_that_is_not_two_to_the_split_is_refused(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    patch_source_manifest(source, lambda m: m.update({"n_cubes": N_CUBES + 1}))
    assert run_import(root, source) == 1
    assert "n_cubes" in capsys.readouterr().err


def test_expect_cubes_that_disagrees_with_the_manifest_is_refused(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    assert run_import(root, source, "--expect-cubes", "16384") == 1
    err = capsys.readouterr().err
    assert "16384" in err and str(N_CUBES) in err


def test_a_transcript_missing_a_cube_is_refused(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    path = source / "transcripts.jsonl"
    write_jsonl(path, [line for line in read_jsonl(path) if line["cube"] != 3])
    assert run_import(root, source) == 1
    err = capsys.readouterr().err
    assert "cube 3" in err and "transcript" in err
    assert not (root / WAVE_DIR).exists()


def test_a_transcript_that_did_not_verify_is_refused(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    path = source / "transcripts.jsonl"
    lines = read_jsonl(path)
    lines[2]["ok"] = False
    write_jsonl(path, lines)
    assert run_import(root, source) == 1
    err = capsys.readouterr().err
    assert "cube 2" in err and "ok" in err


def test_a_transcript_about_another_proof_is_refused(capsys, tmp_path):
    # The transcript's whole value is that a checker read *this* cube's proof.
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    path = source / "transcripts.jsonl"
    lines = read_jsonl(path)
    lines[1]["drat_sha256"] = "a" * 64
    write_jsonl(path, lines)
    assert run_import(root, source) == 1
    err = capsys.readouterr().err
    assert "cube 1" in err and "sha256" in err


def test_a_duplicate_transcript_line_is_refused(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    path = source / "transcripts.jsonl"
    lines = read_jsonl(path)
    write_jsonl(path, lines + [lines[0]])
    assert run_import(root, source) == 1
    assert "more than one transcript" in capsys.readouterr().err


def test_a_duplicate_verdict_is_refused(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    duplicate = read_json(source / "verdicts" / "v00001.json")
    write_json(source / "verdicts" / "v00001-resumed.json", duplicate)
    assert run_import(root, source) == 1
    assert "more than one verdict" in capsys.readouterr().err


def test_a_manifest_of_an_unknown_schema_is_refused(capsys, tmp_path):
    # The live seqcount wave was cut as cube-wave.v1, whose verdicts the gate
    # does not recognise. Importing it would write evidence that cannot pass.
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    patch_source_manifest(source, lambda m: m.update({"schema": "cube-wave.v1"}))
    assert run_import(root, source) == 1
    err = capsys.readouterr().err
    assert "cube-wave.v1" in err and "cube-wave.v2" in err


def test_an_unknown_cube_construction_is_refused(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    patch_source_manifest(
        source, lambda m: m.update({"cube_construction": "gray-code.v9"})
    )
    assert run_import(root, source) == 1
    assert "gray-code.v9" in capsys.readouterr().err


def test_a_malformed_verdict_is_refused(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    (source / "verdicts" / "v00000.json").write_bytes(b"{\"cube\": 0, ")
    assert run_import(root, source) == 1
    assert "will not parse" in capsys.readouterr().err


def test_a_source_that_is_not_a_wave_is_refused(capsys, tmp_path):
    root = make_repo(tmp_path)
    (tmp_path / "empty").mkdir()
    assert run_import(root, tmp_path / "empty") == 1
    assert "manifest.json" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["../elsewhere", "nested/wave", "", "."])
def test_a_name_that_is_not_a_single_directory_is_refused(capsys, tmp_path, name):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    code = import_wave.main(
        ["--source", str(source), "--name", name, "--root", str(root)]
    )
    assert code == 1
    assert "name" in capsys.readouterr().err
    assert not list(root.rglob("manifest.json"))


def test_an_existing_destination_is_refused_rather_than_overwritten(capsys, tmp_path):
    root = make_repo(tmp_path)
    source = write_source_wave(tmp_path / "source")
    assert run_import(root, source) == 0
    capsys.readouterr()
    kept = snapshot(root / WAVE_DIR)
    assert run_import(root, source) == 1
    assert "already" in capsys.readouterr().err
    assert snapshot(root / WAVE_DIR) == kept
