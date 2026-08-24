"""The stall alarm must fire on a crash loop and stay silent otherwise.

Written 2026-08-22, when the campaign spent an unknown stretch being restarted
every 300 s while producing nothing, and no alarm existed that could notice.
The watchdog's other two alarms - a SAT verdict and a check failure - both
require the campaign to be RUNNING well enough to write a file.

This campaign has already shipped two guards that could never fire (a lock named
PRUNER_RUNNING that nothing creates; a single-instance check that matched its
own launcher). So the fire case and the silent case are both asserted here, and
the phase-3 case is the one that matters most: 29 h of legitimate zero-verdict
work must not read as a stall.

Run: python -m pytest test_watchdog_stall.py -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


@pytest.fixture
def wd(monkeypatch, tmp_path):
    """Import watchdog with its paths pointed at a temp dir.

    watchdog.py calls ignore_console_signals() at import; harmless here.
    """
    import watchdog

    monkeypatch.setattr(watchdog, "VERDICTS", str(tmp_path / "w1" / "verdicts"))
    monkeypatch.setattr(watchdog, "VERDICTS2", str(tmp_path / "w2" / "verdicts"))
    monkeypatch.setattr(watchdog, "TRANSCRIPTS", str(tmp_path / "t.jsonl"))
    os.makedirs(tmp_path / "w1" / "verdicts")
    os.makedirs(tmp_path / "w2" / "verdicts")
    return watchdog, tmp_path


def _add_verdicts(tmp_path, wave, n, start=0):
    d = tmp_path / wave / "verdicts"
    for i in range(start, start + n):
        (d / f"v{i:05d}.json").write_text('{"cube": %d, "rc": 20}' % i,
                                          encoding="ascii")


def test_progress_counts_phase_1_and_2_wave_verdicts(wd):
    watchdog, tmp = wd
    assert watchdog.progress() == 0
    _add_verdicts(tmp, "w1", 7)
    assert watchdog.progress() == 7


def test_progress_counts_phase_3_transcripts(wd):
    """Phase 3 writes NO verdicts for ~29 h. If progress ignored transcripts,
    the stall alarm would fire on the healthiest part of the run."""
    watchdog, tmp = wd
    _add_verdicts(tmp, "w1", 3)
    before = watchdog.progress()
    (tmp / "t.jsonl").write_text('{"cube": 1, "ok": true}\n'
                                 '{"cube": 2, "ok": true}\n', encoding="ascii")
    assert watchdog.progress() == before + 2


def test_progress_counts_phase_4_confirmation_wave(wd):
    """Phase 4 grows a DIFFERENT wave directory."""
    watchdog, tmp = wd
    before = watchdog.progress()
    _add_verdicts(tmp, "w2", 5)
    assert watchdog.progress() == before + 5


def test_progress_survives_a_missing_directory(wd):
    """wave274/verdicts does not exist until phase 4 starts. A crash here
    would take down the watchdog, and a dead watchdog looks exactly like a
    quiet one."""
    watchdog, tmp = wd
    watchdog.VERDICTS2 = str(tmp / "does" / "not" / "exist")
    assert watchdog.progress() >= 0


# ---------------------------------------------------------------------------
# The decision itself, as the poll loop computes it.
# ---------------------------------------------------------------------------

def _fires(watchdog, stalled_s, restarts):
    """Call the REAL decision function. Re-implementing the condition here
    would test only that it can be written twice - and the poll loop could
    then drift away from it silently."""
    return watchdog.is_stalled(stalled_s, restarts)


def test_alarm_fires_on_the_2026_08_22_shape(wd):
    """18 restarts in 90 min, zero progress. This is the real event."""
    watchdog, _ = wd
    assert _fires(watchdog, 90 * 60, 18)


def test_alarm_silent_during_a_long_phase_3_proof(wd):
    """A 66-minute proof with 14 workers busy: no progress, but the
    orchestrator was never restarted. Must stay silent."""
    watchdog, _ = wd
    assert not _fires(watchdog, 66 * 60, 0)


def test_alarm_silent_on_restarts_that_are_making_progress(wd):
    """A bad night of console kills still counts as working, because progress
    resets the timer - so stalled_s stays small however many restarts happen."""
    watchdog, _ = wd
    assert not _fires(watchdog, 4 * 60, 12)


def test_alarm_silent_on_a_single_restart_that_recovers(wd):
    watchdog, _ = wd
    assert not _fires(watchdog, 50 * 60, 1)


def test_thresholds_are_sane(wd):
    """A guard nobody can reach is the failure mode this campaign keeps
    hitting. 45 min at ~210/h is ~157 missed verdicts - unambiguous."""
    watchdog, _ = wd
    assert watchdog.STALL_SECONDS >= 1800, "too twitchy for the phase 3 tail"
    assert watchdog.STALL_SECONDS <= 7200, "so slow it wastes a night"
    assert watchdog.STALL_RESTARTS >= 2, "one restart is normal operation"
    assert watchdog.STALL_SECONDS > watchdog.POLL_SECONDS * watchdog.STALL_RESTARTS, \
        "must span enough polls that the restarts can actually accumulate"


def test_stall_notice_names_the_recovery_phrase(wd):
    """The notice is the ONLY channel to the operator. It has to say the words
    that start a session, and must not read as the mathematics failing."""
    watchdog, _ = wd
    assert "continue with erdos" in watchdog.STALL_NOTICE
    assert "NOT the" in watchdog.STALL_NOTICE
