"""A cube-and-conquer wave replaces one UNSAT run with thousands, so the gate's
job changes shape: it is no longer "did a solver say 20" but "is this set of
cubes every case, and did every one of them come back 20".

Each test below starts from a wave that is real - four cubes of a genuinely
UNSAT instance, each refuted by tests/_minisolve.py while the fixture is built -
and breaks exactly one thing. The gate must exit non-zero *and* name the rule.
"""

import gzip
import json
import os
import shutil
import subprocess

import pytest

from gate.verify_all import LEVELS, main
from nk2.cubes import CONSTRUCTION
from tests import _wavefix
from tests._wavefix import (
    CONFIRM_DIR,
    CONFIRM_VERDICTS,
    CONFIRM_VERDICTS_JSONL,
    PRIMARY_DIR,
    TRANSCRIPTS,
    VERDICTS,
    VERDICTS_JSONL,
    build_wave_repo,
    patch_claim,
    patch_manifest,
    patch_transcript,
    patch_verdict,
    verdict_lines,
    write_verdict_lines,
)


def run(root, capsys, extra=()):
    code = main(["--root", str(root), *extra])
    return code, capsys.readouterr().out


def failures(out):
    return [line for line in out.splitlines() if line.startswith("FAIL ")]


# --- the positive control ---------------------------------------------------


def test_a_complete_confirmed_wave_passes(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    code, out = run(root, capsys)
    assert code == 0, out
    assert "FAIL" not in out
    assert "OK 1 claim(s)" in out


def test_wave_with_monolithic_confirmation_passes(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", confirm="unsat_runs")
    code, out = run(root, capsys)
    assert code == 0, out
    assert "FAIL" not in out


def test_upper_bound_wave_without_confirmation_passes(capsys, tmp_path):
    # An upper bound may rest on one encoding. Only an *exact* claim may not.
    root = build_wave_repo(tmp_path / "repo", kind="upper_bound_wave", confirm=None)
    code, out = run(root, capsys)
    assert code == 0, out
    assert "FAIL" not in out


# --- W1: the manifest and the base instance ---------------------------------


def test_w1_base_sha_that_does_not_regenerate_is_refused(capsys, tmp_path):
    # The recorded base is what every cube was derived from. If it does not
    # regenerate, nobody can rebuild the instance the wave decided.
    root = build_wave_repo(tmp_path / "repo")
    patch_manifest(root, lambda m: m["base"].update({"sha256": "0" * 64}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W1 ") for line in failures(out)), out
    assert "regenerat" in out


def test_w1_base_clause_count_that_does_not_regenerate_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    patch_manifest(root, lambda m: m["base"].update({"n_clauses": m["base"]["n_clauses"] + 1}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W1 ") for line in failures(out)), out


def test_w1_split_variable_outside_the_main_variables_is_refused(capsys, tmp_path):
    # var(x_n) = n for n in 1..N; anything above N is an auxiliary variable of
    # one encoder, and splitting on it makes the cube set encoder-specific.
    root = build_wave_repo(tmp_path / "repo")
    patch_manifest(root, lambda m: m.update({"split_vars": [2, 400]}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W1 ") for line in failures(out)), out


def test_w1_cube_count_that_is_not_two_to_the_split_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    patch_manifest(root, lambda m: m.update({"n_cubes": 3}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W1 ") for line in failures(out)), out


def test_w1_unknown_manifest_schema_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    patch_manifest(root, lambda m: m.update({"schema": "cube-wave.v3"}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W1 ") for line in failures(out)), out


# --- W2: the cube set is complete by construction ---------------------------


def test_w2_cube_hash_that_the_split_does_not_produce_is_refused(capsys, tmp_path):
    # The gate re-derives every cube from split_vars and hashes the result. A
    # recorded hash it does not reproduce means the cubes that ran were not the
    # cubes this split defines - and completeness is the whole argument.
    root = build_wave_repo(tmp_path / "repo")
    patch_manifest(root, lambda m: m.update({"cubes_sha256": "f" * 64}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W2 ") for line in failures(out)), out


def test_w2_unknown_cube_construction_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    patch_manifest(root, lambda m: m.update({"cube_construction": "gray-code.v9"}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W2 ") for line in failures(out)), out
    assert CONSTRUCTION in out


# --- W3: every cube came back UNSAT -----------------------------------------


def test_w3_missing_cube_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    (root / PRIMARY_DIR / "verdicts" / "cube0002.json").unlink()
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out
    assert "cube 2" in out


def test_w3_cube_without_a_return_code_is_refused(capsys, tmp_path):
    # The live case: an external timeout kills the solver and the verdict is
    # written with rc null. That is UNKNOWN, and UNKNOWN is not a decomposition.
    root = build_wave_repo(tmp_path / "repo")
    patch_verdict(root, 1, lambda v: v.update({"rc": None}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out


def test_w3_satisfiable_cube_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    patch_verdict(root, 0, lambda v: v.update({"rc": 10}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out


def test_w3_tampered_cube_literals_are_refused(capsys, tmp_path):
    # The verdict says rc 20 for a cube whose literals are not the ones the
    # construction gives for that index: a solver refuted something else.
    root = build_wave_repo(tmp_path / "repo")
    patch_verdict(root, 3, lambda v: v.update({"lits": [2, -5]}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out
    assert "lits" in out or "literals" in out


def test_w3_duplicate_cube_id_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    verdicts = root / PRIMARY_DIR / "verdicts"
    shutil.copyfile(verdicts / "cube0001.json", verdicts / "cube0001-copy.json")
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out


# --- W4: transcripts, when they are on record -------------------------------


def test_w4_transcript_proof_hash_that_disagrees_with_the_verdict_is_refused(capsys, tmp_path):
    # The transcript is a record of a checker reading *a* proof. Unless its
    # sha256 is the one the solver recorded, it is not this cube's proof.
    root = build_wave_repo(tmp_path / "repo")
    patch_transcript(root, 2, lambda line: line.update({"drat_sha256": "a" * 64}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W4 ") for line in failures(out)), out


def test_w4_transcript_that_does_not_say_verified_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    patch_transcript(root, 0, lambda line: line.update({"verdict": "s NOT VERIFIED"}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W4 ") for line in failures(out)), out


def test_w4_missing_transcript_line_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    path = root / TRANSCRIPTS
    lines = path.read_text(encoding="ascii").splitlines()[:-1]
    path.write_bytes(("\n".join(lines) + "\n").encode("ascii"))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W4 ") for line in failures(out)), out


def test_w4_transcript_without_a_checker_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    patch_transcript(root, 1, lambda line: line.update({"checker": ""}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W4 ") for line in failures(out)), out


def test_w4_proof_outside_the_evidence_tree_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    patch_transcript(root, 0, lambda line: line.update({"proof_path_rel": "scratch/cube0.drat.gz"}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W4 ") for line in failures(out)), out
    assert "is outside evidence/" in out


# --- W5: the claim and the manifest have to be about the same thing ---------


def test_w5_value_that_is_not_the_manifest_n_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", kind="upper_bound_wave", confirm=None)
    patch_claim(root, lambda claim: claim.update({"value": 10}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W5 ") for line in failures(out)), out


def test_w5_manifest_for_a_different_k_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    patch_manifest(root, lambda m: m.update({"k": 4}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W5 ") for line in failures(out)), out


def test_w5_upper_bound_wave_without_a_wave_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", kind="upper_bound_wave", confirm=None)
    patch_claim(root, lambda claim: claim.update({"wave": None}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W5 ") for line in failures(out)), out


def test_w5_lower_bound_with_a_wave_is_refused(capsys, tmp_path):
    # An UNSAT decomposition says nothing about the lower side, so a lower_bound
    # claim carrying one is either confused or dressing itself up.
    root = build_wave_repo(tmp_path / "repo")
    patch_claim(
        root,
        lambda claim: claim.update(
            {"kind": "lower_bound", "value": 9, "evidence_level": "witness"}
        ),
    )
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W5 ") for line in failures(out)), out


def test_w5_plain_upper_bound_carrying_a_wave_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", kind="upper_bound_wave", confirm=None)
    patch_claim(root, lambda claim: claim.update({"kind": "upper_bound"}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W5 ") for line in failures(out)), out


def test_w5_exact_claim_still_needs_its_witness(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    patch_claim(root, lambda claim: claim.update({"witness": None}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL G2 ") for line in failures(out)), out


# --- W6: no exact claim without two encoders --------------------------------


def test_w6_exact_claim_without_confirmation_is_refused(capsys, tmp_path):
    # Both sides are present and every cube is genuinely UNSAT. It is still one
    # encoding, and an encoder bug would be invisible to every cube of it.
    root = build_wave_repo(tmp_path / "repo", confirm=None)
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W6 ") for line in failures(out)), out


def test_w6_exact_claim_without_confirmation_is_refused_at_any_declared_level(capsys, tmp_path):
    # Declaring less evidence does not buy the word "exact". The rule is about
    # what the claim asserts, not about how loudly it asserts it.
    root = build_wave_repo(tmp_path / "repo", confirm=None, evidence_level="witness")
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W6 ") for line in failures(out)), out


def test_w6_confirmation_from_the_same_encoder_is_refused(capsys, tmp_path):
    # A wave that is complete, internally consistent and entirely correct - and
    # cut from the same encoder as the one it is meant to be confirming.
    root = build_wave_repo(tmp_path / "repo")
    _wavefix.write_wave(root, CONFIRM_DIR, "seqcount", False, False)
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W6 ") for line in failures(out)), out
    assert "same encoder" in out


def test_w6_monolithic_confirmation_from_the_same_encoder_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", confirm="unsat_runs")
    # Rebuild the wave around the encoder the confirming run-log used.
    _wavefix.write_wave(root, PRIMARY_DIR, "subsets", True, True)
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W6 ") for line in failures(out)), out


def test_w6_broken_confirmation_wave_is_refused(capsys, tmp_path):
    # The confirming wave is checked exactly as hard as the primary one.
    root = build_wave_repo(tmp_path / "repo")
    (root / CONFIRM_DIR / "verdicts" / "cube0000.json").unlink()
    code, out = run(root, capsys)
    assert code != 0, out
    assert failures(out), out
    assert "confirm" in out


def test_w6_confirmation_wave_at_the_wrong_n_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    _wavefix.write_wave(root, CONFIRM_DIR, "totalizer", False, False)
    _wavefix.patch_manifest(root, lambda m: m.update({"N": 8}), CONFIRM_DIR)
    code, out = run(root, capsys)
    assert code != 0, out
    assert failures(out), out


# --- evidence levels --------------------------------------------------------


def test_levels_place_the_wave_tiers_where_the_tdd_says():
    # The exact tuple is pinned in test_gate.py. What matters here is the
    # ordering decision: a single-encoder wave, however completely proof-checked,
    # does not outrank two encodings agreeing, because a checked proof says
    # nothing about whether the CNF is the problem.
    order = {name: i for i, name in enumerate(LEVELS)}
    assert order["witness"] < order["unsat-wave"] < order["wave-drat-verified"]
    assert order["wave-drat-verified"] < order["unsat-dual"] < order["drat-transcript"]
    assert order["drat-transcript"] < order["drat-reverified"]


@pytest.mark.parametrize(
    ("confirm", "transcripts", "level"),
    [
        (None, False, "unsat-wave"),
        (None, True, "wave-drat-verified"),
        ("wave", False, "unsat-dual"),
        ("wave", True, "drat-transcript"),
    ],
    ids=["bare", "proofs", "confirmed", "confirmed-proofs"],
)
def test_a_wave_reaches_exactly_the_level_it_earns(capsys, tmp_path, confirm, transcripts, level):
    root = build_wave_repo(
        tmp_path / "repo",
        kind="upper_bound_wave",
        confirm=confirm,
        transcripts=transcripts,
        evidence_level=level,
    )
    code, out = run(root, capsys)
    assert code == 0, out
    assert "understates" not in out, out

    # And one tier higher is an overstatement the gate refuses.
    higher = LEVELS[LEVELS.index(level) + 1]
    patch_claim(root, lambda claim: claim.update({"evidence_level": higher}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL G7 ") for line in failures(out)), out


def test_a_wave_that_fails_lends_no_evidence_level(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", kind="upper_bound_wave", confirm=None)
    patch_verdict(root, 2, lambda v: v.update({"rc": 20, "lits": [-2, -5]}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out


# --- a wave is one directory ------------------------------------------------
#
# A verdict names no instance and no encoder: it is `{cube, lits, rc, wall_s,
# drat_sha256, drat_bytes}`, and `lits` is a function of the split alone, so two
# waves sharing a split write byte-identical verdicts. The only thing that can
# tie a body of solving to the instance it decided is where it sits, which is
# why the manifest's own directory - and nothing else under evidence/waves/ -
# is where that wave's verdicts, transcripts and proofs are read from.


def test_confirming_wave_reading_the_primary_wave_verdicts_is_refused(capsys, tmp_path):
    # The reported exploit, and the one that matters: nk2 writes a manifest with
    # no solver in the room, so a "second encoder" whose verdicts_dir is the
    # first encoder's is one file plus somebody else's work. It bought an exact
    # claim at drat-transcript.
    root = build_wave_repo(tmp_path / "repo")
    shutil.rmtree(root / CONFIRM_DIR / "verdicts")
    patch_claim(root, lambda claim: claim["wave"]["confirm"].update({"verdicts_dir": VERDICTS}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert failures(out), out
    assert "confirm wave" in out


def test_wave_verdicts_from_a_different_wave_are_refused(capsys, tmp_path):
    # The accident this guards against: a claim block pasted from the previous
    # encoder's wave, keeping its verdicts_dir. Every verdict is genuine and
    # decided a different instance.
    root = build_wave_repo(tmp_path / "repo")
    patch_claim(root, lambda claim: claim["wave"].update({"verdicts_dir": CONFIRM_VERDICTS}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out
    assert f"is outside {PRIMARY_DIR}/" in out


def test_wave_transcripts_from_a_different_wave_are_refused(capsys, tmp_path):
    # Every line of the borrowed transcripts is rewritten to name this wave's
    # own proofs, so the location of the file itself is the only thing wrong.
    # Without that the proof rule below would be what caught it, and this test
    # would be testing a rule it does not name.
    root = build_wave_repo(tmp_path / "repo")
    _wavefix.write_wave(root, CONFIRM_DIR, "totalizer", False, True)
    path = root / CONFIRM_DIR / "transcripts.jsonl"
    lines = [json.loads(raw) for raw in path.read_text(encoding="ascii").splitlines() if raw]
    for line in lines:
        line["proof_path_rel"] = line["proof_path_rel"].replace(CONFIRM_DIR, PRIMARY_DIR)
    path.write_bytes(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines).encode("ascii")
    )
    patch_claim(
        root,
        lambda claim: claim["wave"].update({"transcripts": f"{CONFIRM_DIR}/transcripts.jsonl"}),
    )
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W4 ") for line in failures(out)), out
    assert f"is outside {PRIMARY_DIR}/" in out


def test_w4_proof_from_a_different_wave_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    _wavefix.write_wave(root, CONFIRM_DIR, "totalizer", False, True)
    patch_transcript(
        root, 0,
        lambda line: line.update({"proof_path_rel": f"{CONFIRM_DIR}/proofs/cube0000.drat.gz"}),
    )
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W4 ") for line in failures(out)), out


def test_manifest_that_does_not_name_its_own_directory_is_refused(capsys, tmp_path):
    # Two manifests in one directory would let two waves share a body of
    # verdicts and satisfy the containment rule while doing it, so a wave
    # directory holds exactly one manifest and it is called manifest.json.
    root = build_wave_repo(tmp_path / "repo", kind="upper_bound_wave", confirm=None)
    shutil.move(str(root / PRIMARY_DIR / "manifest.json"), str(root / PRIMARY_DIR / "other.json"))
    patch_claim(root, lambda claim: claim["wave"].update({"manifest": f"{PRIMARY_DIR}/other.json"}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W1 ") for line in failures(out)), out
    assert "manifest.json" in out


def test_confirming_wave_in_the_primary_wave_directory_is_refused(capsys, tmp_path):
    # One directory, one manifest, one encoder: a confirm block that names the
    # primary's own directory cannot be a second encoding of anything.
    root = build_wave_repo(tmp_path / "repo")
    shutil.rmtree(root / CONFIRM_DIR)
    patch_claim(
        root,
        lambda claim: claim["wave"]["confirm"].update(
            {"manifest": _wavefix.MANIFEST, "verdicts_dir": VERDICTS}
        ),
    )
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W6 ") for line in failures(out)), out


# --- paths and hygiene ------------------------------------------------------


def test_wave_manifest_outside_the_waves_tree_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", kind="upper_bound_wave", confirm=None)
    shutil.copytree(root / PRIMARY_DIR, root / "scratch" / "wave")
    patch_claim(
        root,
        lambda claim: claim["wave"].update(
            {
                "manifest": "scratch/wave/manifest.json",
                "verdicts_dir": "scratch/wave/verdicts",
                "transcripts": "scratch/wave/transcripts.jsonl",
            }
        ),
    )
    code, out = run(root, capsys)
    assert code != 0, out
    assert "is outside evidence/waves/" in out


def test_wave_artifacts_are_not_reported_as_unreferenced(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    code, out = run(root, capsys)
    assert code == 0, out
    assert "WARN" not in out, out


def test_gzipped_proofs_do_not_trip_the_ascii_scan(capsys, tmp_path):
    # A .drat.gz is bytes, not text. G6 must skip it the way it skips a .drat,
    # or every wave with its proofs still on disk reddens the gate.
    root = build_wave_repo(tmp_path / "repo")
    assert list((root / PRIMARY_DIR / "proofs").glob("*.drat.gz"))
    code, out = run(root, capsys)
    assert code == 0, out
    assert "FAIL G6" not in out


def test_reverify_without_a_checker_binary_is_an_info_not_a_failure(capsys, tmp_path, monkeypatch):
    # drat-trim is application-control blocked on the development machine, so
    # the skip path is the one that actually runs here. It must stay green.
    monkeypatch.setenv("NK2_DRAT_TRIM_DIR", "")
    monkeypatch.setattr("gate.verify_all.find_drat_trim", lambda: None)
    root = build_wave_repo(tmp_path / "repo")
    code, out = run(root, capsys, extra=["--reverify-drat"])
    assert code == 0, out
    assert "INFO" in out and "reverify" in out


def stub_checker(directory, verdict_line: str) -> str:
    """A checker that prints one line, so the re-verification path can be run.

    drat-trim is application-control blocked on the development machine and
    absent from CI, so without this the whole --reverify-drat path for waves
    would never execute anywhere. It answers the questions that do not need a
    real checker: does the gate decompress the proof, does it hash it against
    the transcript, does it rebuild the cube instance, and does it believe the
    answer it gets.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        path = directory / "stub-checker.bat"
        path.write_text(f"@echo off\r\necho {verdict_line}\r\n", encoding="ascii")
    else:
        path = directory / "stub-checker.sh"
        path.write_text(f"#!/bin/sh\necho '{verdict_line}'\n", encoding="ascii")
        path.chmod(0o755)
    return str(path)


def test_reverify_decompresses_and_rechecks_every_proof(capsys, tmp_path, monkeypatch):
    checker = stub_checker(tmp_path / "bin", "s VERIFIED")
    monkeypatch.setattr("gate.verify_all.find_drat_trim", lambda: checker)
    calls = []
    real_run = subprocess.run

    def spy(args, **kwargs):
        calls.append(args)
        return real_run(args, **kwargs)

    monkeypatch.setattr("gate.verify_all.subprocess.run", spy)
    root = build_wave_repo(tmp_path / "repo")
    code, out = run(root, capsys, extra=["--reverify-drat"])
    assert code == 0, out
    # One call per cube, each handed a rebuilt cube instance and a proof that
    # only exists because the gate decompressed it.
    assert len(calls) == 4, calls
    for args in calls:
        assert args[0] == checker
        assert args[1].endswith("cube.cnf") and args[2].endswith("cube.drat")


def test_reverify_refuses_a_proof_the_transcript_does_not_describe(capsys, tmp_path, monkeypatch):
    checker = stub_checker(tmp_path / "bin", "s VERIFIED")
    monkeypatch.setattr("gate.verify_all.find_drat_trim", lambda: checker)
    root = build_wave_repo(tmp_path / "repo")
    proof = root / PRIMARY_DIR / "proofs" / "cube0001.drat.gz"
    with gzip.GzipFile(filename="", mode="wb", fileobj=proof.open("wb"), mtime=0) as out:
        out.write(b"c substituted\n0\n")
    code, out = run(root, capsys, extra=["--reverify-drat"])
    assert code != 0, out
    assert any(line.startswith("FAIL W4 ") for line in failures(out)), out
    assert "not the one the transcript records" in out


def test_reverify_refuses_a_proof_the_checker_rejects(capsys, tmp_path, monkeypatch):
    checker = stub_checker(tmp_path / "bin", "s NOT VERIFIED")
    monkeypatch.setattr("gate.verify_all.find_drat_trim", lambda: checker)
    root = build_wave_repo(tmp_path / "repo")
    code, out = run(root, capsys, extra=["--reverify-drat"])
    assert code != 0, out
    assert any(line.startswith("FAIL W4 ") for line in failures(out)), out


def test_reverify_says_so_when_the_proofs_are_gone(capsys, tmp_path, monkeypatch):
    # The campaign deletes proofs once a checker has read them, so this is the
    # ordinary case, not an edge one. It must be an INFO with a count, not a
    # silent pass and not a failure.
    checker = stub_checker(tmp_path / "bin", "s VERIFIED")
    monkeypatch.setattr("gate.verify_all.find_drat_trim", lambda: checker)
    root = build_wave_repo(tmp_path / "repo")
    shutil.rmtree(root / PRIMARY_DIR / "proofs")
    code, out = run(root, capsys, extra=["--reverify-drat"])
    assert code == 0, out
    assert "4 proof(s) are not on disk" in out


def test_wave_claim_schema_is_checked_before_anything_reads_it(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo")
    patch_claim(root, lambda claim: claim["wave"].pop("confirm"))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL G1 ") for line in failures(out)), out


# --- consolidated verdicts --------------------------------------------------
#
# A wave of sixteen thousand cubes cannot be committed as sixteen thousand
# files, so `verdicts_dir` may instead name one `.jsonl` holding the same
# objects, one per line. Every W3 rule has to hold identically over it: the same
# cube ids, the same rc 20, the same literals, and the same refusal of a
# duplicate, an extra or a line that will not parse. These tests are the
# directory-form ones above, repeated against the consolidated shape.


def test_a_complete_confirmed_wave_in_the_consolidated_form_passes(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", verdicts_form="jsonl")
    assert not (root / PRIMARY_DIR / "verdicts").exists()
    assert (root / VERDICTS_JSONL).is_file()
    code, out = run(root, capsys)
    assert code == 0, out
    assert "FAIL" not in out
    assert "WARN" not in out, out


@pytest.mark.parametrize(
    ("confirm", "transcripts", "level"),
    [
        (None, False, "unsat-wave"),
        (None, True, "wave-drat-verified"),
        ("wave", False, "unsat-dual"),
        ("wave", True, "drat-transcript"),
    ],
    ids=["bare", "proofs", "confirmed", "confirmed-proofs"],
)
def test_consolidated_verdicts_earn_the_same_levels(
    capsys, tmp_path, confirm, transcripts, level
):
    # The form the verdicts are stored in is bookkeeping. It must not move a
    # claim up or down the ladder.
    root = build_wave_repo(
        tmp_path / "repo",
        kind="upper_bound_wave",
        confirm=confirm,
        transcripts=transcripts,
        evidence_level=level,
        verdicts_form="jsonl",
    )
    code, out = run(root, capsys)
    assert code == 0, out
    assert "understates" not in out, out

    higher = LEVELS[LEVELS.index(level) + 1]
    patch_claim(root, lambda claim: claim.update({"evidence_level": higher}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL G7 ") for line in failures(out)), out


def test_w3_jsonl_missing_cube_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", verdicts_form="jsonl")
    write_verdict_lines(root, [line for line in verdict_lines(root) if line["cube"] != 2])
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out
    assert "cube 2" in out


def test_w3_jsonl_cube_without_a_return_code_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", verdicts_form="jsonl")
    patch_verdict(root, 1, lambda v: v.update({"rc": None}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out


def test_w3_jsonl_tampered_cube_literals_are_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", verdicts_form="jsonl")
    patch_verdict(root, 3, lambda v: v.update({"lits": [2, -5]}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out
    assert "lits" in out or "literals" in out


def test_w3_jsonl_duplicate_cube_line_is_refused(capsys, tmp_path):
    # The consolidated file is appended to as cubes land, so a wave that was
    # resumed can hold a cube twice. Two lines for one cube means one of them is
    # about a run nobody can identify, and a set with a duplicate is not a
    # partition on record.
    root = build_wave_repo(tmp_path / "repo", verdicts_form="jsonl")
    lines = verdict_lines(root)
    write_verdict_lines(root, lines + [lines[1]])
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out
    assert "more than one verdict" in out


def test_w3_jsonl_cube_id_beyond_the_cube_count_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", verdicts_form="jsonl")
    lines = verdict_lines(root)
    extra = dict(lines[0])
    extra["cube"] = len(lines)
    write_verdict_lines(root, lines + [extra])
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out
    assert "outside 0.." in out


@pytest.mark.parametrize(
    "junk", ['{"cube": 2, "rc"', "", "   "], ids=["truncated", "blank", "whitespace"]
)
def test_w3_jsonl_line_that_is_not_a_verdict_is_refused_not_skipped(capsys, tmp_path, junk):
    # Every cube is present and correct, and one extra line is not a verdict.
    # A reader that skips what it cannot parse passes this wave and says
    # nothing, which is how a truncated write - the ordinary way a file ends
    # when a machine dies mid-append - stops being visible. The line count is
    # part of the record, so an unreadable line is a failure and never a skip.
    root = build_wave_repo(tmp_path / "repo", verdicts_form="jsonl")
    path = root / VERDICTS_JSONL
    path.write_bytes(path.read_bytes() + (junk + "\n").encode("ascii"))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out


def test_w3_jsonl_line_with_the_wrong_keys_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", verdicts_form="jsonl")
    lines = verdict_lines(root)
    lines[0].pop("wall_s")
    write_verdict_lines(root, lines)
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out


def test_w3_jsonl_verdicts_from_a_different_wave_are_refused(capsys, tmp_path):
    # The manifest-binding rule is the same rule in the consolidated form: a
    # verdict names no instance, so where the file sits is the only thing that
    # says which wave it is about.
    root = build_wave_repo(tmp_path / "repo", verdicts_form="jsonl")
    patch_claim(root, lambda claim: claim["wave"].update({"verdicts_dir": CONFIRM_VERDICTS_JSONL}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out
    assert f"is outside {PRIMARY_DIR}/" in out


def test_w3_jsonl_that_does_not_exist_is_refused(capsys, tmp_path):
    root = build_wave_repo(tmp_path / "repo", verdicts_form="jsonl")
    (root / VERDICTS_JSONL).unlink()
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out


def test_w3_verdicts_named_jsonl_that_are_a_directory_are_refused(capsys, tmp_path):
    # `verdicts.jsonl` is read as the consolidated file, and a directory of that
    # name is not one. Deciding the form from the recorded name rather than from
    # what happens to be on disk keeps one claim readable one way.
    root = build_wave_repo(tmp_path / "repo", kind="upper_bound_wave", confirm=None)
    shutil.move(str(root / PRIMARY_DIR / "verdicts"), str(root / PRIMARY_DIR / "verdicts.jsonl"))
    patch_claim(root, lambda claim: claim["wave"].update({"verdicts_dir": VERDICTS_JSONL}))
    code, out = run(root, capsys)
    assert code != 0, out
    assert any(line.startswith("FAIL W3 ") for line in failures(out)), out


def test_a_claim_with_no_wave_key_still_verifies(capsys, tmp_path):
    # Every claim on record predates waves and carries no `wave` key at all.
    # Absent has to keep meaning null, or the committed claims stop verifying.
    root = tmp_path / "repo"
    shutil.copytree(_wavefix.GOOD, root)
    assert "wave" not in json.loads(
        (root / "claims" / "CLAIMS.json").read_text(encoding="ascii")
    )["claims"][0]
    code, out = run(root, capsys)
    assert code == 0, out
