"""A MISSING PROOF IS NOT A FAILED PROOF.

On 2026-08-23 01:09 CHECK_FAILURES.jsonl gained one line -
{"cube": 1752, "ok": false, "error": "proof missing"} - and the watchdog
correctly refused to restart anything, because that file means the mathematics
failed after four days of compute. It had not. Cube 1752 was ALREADY VERIFIED
("s VERIFIED", tool_rc 0) and its 161 MB proof had then been legitimately
deleted by the pruner, whose entire job is verify / record the sha / reclaim
the disk. check_pass builds its skip-list from transcripts.jsonl ONCE at
startup, so a proof reclaimed after that read looks like a missing proof.

These tests pin the classification, and half of them exist to keep the fix
NARROW: a real "s NOT VERIFIED" and a real sha mismatch MUST still halt the
campaign. A guard that swallowed those would be far worse than the bug.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import check_pass  # noqa: E402

# IMPLEMENTED 2026-08-24. These tests were written first and spent a day as
# `pytest.mark.xfail(strict=True)`, because on Windows a ProcessPoolExecutor
# respawns its workers by RE-IMPORTING the module from disk, so editing
# check_pass while a pass is running can split behaviour across workers.
#
# The deferral was lifted on a MEASUREMENT, not on the calendar. The recorded
# reason was "check_pass has four live processes"; a process census taken
# 2026-08-24 19:35 found zero, and no live module imports check_pass either -
# cube_wave2 imports verdict_io, sample_prune imports check_and_prune, and
# run_campaign only ever SPAWNS check_pass.py as a subprocess, at phase 3.
# Phase 1 had ~2.5 days left at the time, so nothing could re-import this file
# mid-edit. The hazard is real; it just was not present.
#
# strict=True was the mechanism that made this happen rather than being
# forgotten: the moment `classify` existed all eleven turned into XPASS, which
# pytest reports as a FAILURE, and the marker had to be deleted by whoever did
# the work. Observed doing exactly that before it was removed. A marker that
# cannot outlive the gap it describes is worth more than a TODO comment.


@pytest.fixture
def trans(tmp_path):
    """A transcripts.jsonl holding one already-verified cube, 1752 - the real
    cube that halted the campaign, with its real recorded shape."""
    p = tmp_path / "transcripts.jsonl"
    p.write_text(
        json.dumps({"cube": 1752, "ok": True, "tool": "drat-trim",
                    "tool_rc": 0, "verdict": "s VERIFIED"}) + "\n"
        + json.dumps({"cube": 12, "ok": True, "tool": "drat-trim",
                      "tool_rc": 0, "verdict": "s VERIFIED"}) + "\n",
        encoding="ascii")
    return str(p)


# ---------------------------------------------------------------- the bug ---

def test_missing_proof_for_an_already_verified_cube_is_a_skip(trans):
    """THE 01:09 HALT. This is the whole point of the fix."""
    r = {"cube": 1752, "ok": False, "missing_proof": True,
         "error": "proof missing"}
    assert check_pass.classify(r, trans) == "skip-already-verified"


def test_missing_proof_for_an_unverified_cube_is_an_environment_fault(trans):
    """Not in transcripts: we genuinely cannot judge a proof that is not on
    disk. That is CHECKER_ERRORS and rc 3 (environment fault), which
    run_campaign reports separately - never CHECK_FAILURES."""
    r = {"cube": 999, "ok": False, "missing_proof": True,
         "error": "proof missing"}
    assert check_pass.classify(r, trans) == "checker-error"


def test_the_transcripts_file_is_RE_READ_not_snapshotted(tmp_path):
    """The bug is precisely that the skip-list is built once at startup. If
    classify() read a cached set this test would pass anyway - so it writes the
    cube into the file AFTER the first call and demands the answer changes."""
    p = tmp_path / "transcripts.jsonl"
    p.write_text("", encoding="ascii")
    r = {"cube": 77, "ok": False, "missing_proof": True,
         "error": "proof missing"}
    assert check_pass.classify(r, str(p)) == "checker-error"
    with open(p, "a", encoding="ascii") as fh:
        fh.write(json.dumps({"cube": 77, "ok": True,
                             "verdict": "s VERIFIED"}) + "\n")
    assert check_pass.classify(r, str(p)) == "skip-already-verified", \
        "classify must re-read transcripts.jsonl, not cache it"


# ------------------------------------------- keeping the guard NARROW ---

def test_a_real_unverified_proof_STILL_halts(trans):
    """The mathematics failing must still reach CHECK_FAILURES. If this ever
    returns anything else, the campaign can report success on a bad proof."""
    r = {"cube": 1752, "ok": False, "tool": "drat-trim", "tool_rc": 1,
         "verdict": "s NOT VERIFIED"}
    assert check_pass.classify(r, trans) == "check-failure"


def test_a_sha_mismatch_STILL_halts(trans):
    """A proof that does not hash to what was recorded at solve time is a
    corrupted or substituted proof. Halt, even though the cube IS in
    transcripts - being previously verified must not launder a bad artefact."""
    r = {"cube": 1752, "ok": False, "error": "sha mismatch aaaa vs bbbb"}
    assert check_pass.classify(r, trans) == "check-failure"


def test_a_drat_trim_timeout_STILL_halts(trans):
    r = {"cube": 5, "ok": False, "error": "drat-trim timeout 3600s"}
    assert check_pass.classify(r, trans) == "check-failure"


def test_a_verified_proof_goes_to_the_transcript(trans):
    r = {"cube": 5, "ok": True, "tool": "drat-trim", "tool_rc": 0,
         "verdict": "s VERIFIED"}
    assert check_pass.classify(r, trans) == "transcript"


def test_missing_proof_marker_is_required_not_the_error_string(trans):
    """Classification keys off an explicit flag, not off matching the words
    'proof missing' in a free-text error field. A string match would also
    catch a drat-trim message that happened to contain those words."""
    r = {"cube": 1752, "ok": False, "error": "proof missing"}
    assert check_pass.classify(r, trans) == "check-failure", \
        "without the explicit flag this must stay a failure"


# ----------------------------------------------------- check_one's row ---

def test_check_one_flags_a_missing_proof(tmp_path, monkeypatch):
    """check_one must emit the flag classify() keys on. Exercised with a real
    absent proof, so no drat-trim and no pool are involved."""
    monkeypatch.setattr(check_pass, "HERE", str(tmp_path))
    w = tmp_path / "wv"
    (w / "drat").mkdir(parents=True)
    (w / "base.cnf").write_text("p cnf 1 1\n1 0\n", encoding="ascii")
    r = check_pass.check_one(("wv", 448, [1], "deadbeef"))
    assert r["cube"] == 448
    assert r["ok"] is False
    assert r.get("missing_proof") is True, \
        "check_one must mark a missing proof so it cannot be read as a " \
        "failed proof"


def test_transcripts_absent_is_not_verified(tmp_path):
    r = {"cube": 1, "ok": False, "missing_proof": True}
    assert check_pass.classify(
        r, str(tmp_path / "nope.jsonl")) == "checker-error"


def test_a_corrupt_transcript_line_does_not_crash_classification(tmp_path):
    """transcripts.jsonl is appended to by 14 concurrent workers. A torn last
    line must not take the classifier down - that would turn a cosmetic write
    artefact into a dead pass."""
    p = tmp_path / "transcripts.jsonl"
    p.write_text(json.dumps({"cube": 1752, "ok": True}) + "\n"
                 + '{"cube": 99, "ok": tr', encoding="ascii")
    r = {"cube": 1752, "ok": False, "missing_proof": True}
    assert check_pass.classify(r, str(p)) == "skip-already-verified"


# ------------------------------------------------- the wiring, end to end ---
#
# classify() is worth nothing unless run_batch actually routes through it. A
# classifier that nothing consulted would leave every test above green and the
# 01:09 halt completely unfixed - which is the difference between a fix and
# decoration, so it gets its own coverage rather than an assumption.
#
# These drive the REAL pool and the REAL check_one over a genuinely absent
# proof. check_one returns on the missing file before drat-trim is ever
# invoked, so they stay hermetic and take about a second.
#
# The wave directory is passed ABSOLUTE on purpose. check_one does
# os.path.join(HERE, wavedir), and inside a pool worker HERE is the live
# scratch directory; an absolute second argument makes os.path.join discard it.
# That is what stops this test from building fixtures inside the running
# campaign - the exact hazard pyproject.toml refuses to collect other scratch
# tests for.

def _wave(tmp_path):
    w = tmp_path / "wv"
    (w / "drat").mkdir(parents=True)
    (w / "base.cnf").write_text("p cnf 1 1\n1 0\n", encoding="ascii")
    return w


def test_run_batch_does_not_halt_on_a_proof_the_pruner_reclaimed(tmp_path):
    """THE 01:09 HALT, end to end. CHECK_FAILURES.jsonl halts the campaign and
    stops the watchdog restarting anything, so the file must not even be
    created."""
    w = _wave(tmp_path)
    trans = w / "transcripts.jsonl"
    trans.write_text(json.dumps({"cube": 1752, "ok": True,
                                 "verdict": "s VERIFIED"}) + "\n",
                     encoding="ascii")
    fails = w / "CHECK_FAILURES.jsonl"
    errlog = w / "CHECKER_ERRORS.jsonl"
    state = {"nfail": 0, "nskip": 0, "abort": False}

    errored = check_pass.run_batch([(str(w), 1752, [1], "deadbeef")], "main",
                                   1, str(trans), str(fails), str(errlog),
                                   state)

    assert not fails.exists(), \
        "a proof the pruner reclaimed must NEVER reach CHECK_FAILURES.jsonl"
    assert state["nfail"] == 0
    assert state["nskip"] == 1
    assert errored == [], "an already-verified cube needs no retry"


def test_run_batch_calls_an_unjudgeable_proof_an_environment_fault(tmp_path):
    """Never verified and no proof on disk: we cannot judge it either way. That
    is CHECKER_ERRORS and rc 3, which run_campaign reports separately - calling
    it a failed proof is how a bad afternoon gets mistaken for a result."""
    w = _wave(tmp_path)
    trans = w / "transcripts.jsonl"
    trans.write_text("", encoding="ascii")
    fails = w / "CHECK_FAILURES.jsonl"
    errlog = w / "CHECKER_ERRORS.jsonl"
    state = {"nfail": 0, "nskip": 0, "abort": False}

    job = (str(w), 999, [1], "deadbeef")
    errored = check_pass.run_batch([job], "main", 1, str(trans), str(fails),
                                   str(errlog), state)

    assert not fails.exists(), \
        "an unjudgeable proof is an environment fault, not a failed proof"
    assert errlog.exists(), "it must be recorded somewhere, not swallowed"
    assert json.loads(errlog.read_text(encoding="ascii"))["cube"] == 999
    assert errored == [job], "and handed back for the one retry"
    assert state["nfail"] == 0


def test_run_batch_still_halts_on_a_proof_that_did_not_verify(tmp_path):
    """The narrow half. A real check failure must still reach CHECK_FAILURES,
    or the campaign can report success over a bad proof."""
    w = _wave(tmp_path)
    trans = w / "transcripts.jsonl"
    trans.write_text("", encoding="ascii")
    fails = w / "CHECK_FAILURES.jsonl"
    errlog = w / "CHECKER_ERRORS.jsonl"
    state = {"nfail": 0, "nskip": 0, "abort": False}

    # A proof that IS on disk but hashes to nothing like the recorded digest -
    # check_one reaches the sha comparison and returns a plain failure, with no
    # missing_proof flag for the classifier to key on.
    (w / "drat" / "cube_00007.drat").write_text("0\n", encoding="ascii")
    job = (str(w), 7, [1], "deadbeef")
    errored = check_pass.run_batch([job], "main", 1, str(trans), str(fails),
                                   str(errlog), state)

    assert fails.exists(), "a sha mismatch must still halt the campaign"
    assert "sha mismatch" in fails.read_text(encoding="ascii")
    assert state["nfail"] == 1
    assert state["nskip"] == 0
    assert errored == []
