"""A RESULT THAT IS COMPUTED AND THROWN AWAY IS WORSE THAN ONE NEVER COMPUTED.

sample_prune's stand-down path used to call

    ex.shutdown(wait=False, cancel_futures=True)
    break

on seeing STOP_CHECKER. `cancel_futures` reads like a guarantee and is not one:
it cannot cancel work already handed to the pool's call queue (max_workers + 1
items) or the call already executing. Those run to completion - and
check_and_prune.check_one DELETES the proof on success for any cube off the
archive stride. Breaking out of the as_completed loop meant their rows were
never consumed, so the outcome was: proof deleted, no transcript line, no prune
record. That is exactly the cube-448 signature that could not be explained on
2026-08-23, and it is silent - indistinguishable from a cube never checked.

These tests use real concurrent.futures.Future objects rather than a pool. A
Future's result/exception/cancellation semantics are the thing under test, and
constructing them directly keeps this hermetic and instant - no subprocess, no
drat-trim, nothing near the live campaign.
"""
import json
import os
import sys
import threading
import time
from concurrent.futures import Future

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sample_prune  # noqa: E402


def _done(value):
    f = Future()
    f.set_result(value)
    return f


def _failed(exc):
    f = Future()
    f.set_exception(exc)
    return f


def _cancelled():
    f = Future()
    assert f.cancel(), "a pending Future must be cancellable"
    return f


def _row(cube, ok=True):
    return {"cube": cube, "ok": ok, "tool": "drat-trim",
            "verdict": "s VERIFIED" if ok else "s NOT VERIFIED"}


class _FakeExecutor:
    """Records that shutdown was asked for, and how."""

    def __init__(self):
        self.calls = []

    def shutdown(self, wait=True, cancel_futures=False):
        self.calls.append({"wait": wait, "cancel_futures": cancel_futures})


# --------------------------------------------------------------- drain ---

def test_drain_yields_an_inflight_result_that_would_otherwise_be_lost():
    """THE BUG. This row is the one the old code discarded."""
    fut = _done(_row(448))
    futmap = {fut: ("wv", 448, [1], "abc")}
    out = list(sample_prune.drain(futmap, consumed=set()))
    assert len(out) == 1
    job, row, err = out[0]
    assert job[1] == 448
    assert row["cube"] == 448
    assert err is None


def test_a_running_check_cannot_be_cancelled():
    """The premise of the whole fix, asserted rather than assumed. Once a call
    is executing, cancel() refuses - so `cancel_futures=True` leaves it running,
    and it deletes the proof when it finishes."""
    fut = Future()
    assert fut.set_running_or_notify_cancel() is True
    assert fut.cancel() is False, \
        "if this ever passes, cancel_futures would be the guarantee it looks " \
        "like and the drain would be unnecessary"
    assert not fut.cancelled()


def test_drain_WAITS_for_a_check_that_is_still_running():
    """THE CASE THE FIX EXISTS FOR, and the one a first version of these tests
    missed entirely: every other future here is already resolved, so a drain
    that only collected finished futures passed all of them. Mutation testing
    caught that hole - `if ... or not fut.done(): continue` survived.

    A check still executing is precisely the one whose proof is about to be
    deleted, so drain must BLOCK for it rather than skip it.
    """
    fut = Future()
    assert fut.set_running_or_notify_cancel(), "must be in the RUNNING state"

    def finish():
        time.sleep(0.05)  # so result() genuinely has to wait
        fut.set_result(_row(448))

    worker = threading.Thread(target=finish)
    worker.start()
    try:
        out = list(sample_prune.drain({fut: ("wv", 448, [1], "abc")},
                                      consumed=set()))
    finally:
        worker.join(10)
    assert len(out) == 1, "a running check must be waited for, never skipped"
    assert out[0][1]["cube"] == 448


def test_drain_skips_what_the_main_loop_already_consumed():
    """Otherwise every row banked normally would be banked twice."""
    fut = _done(_row(12))
    futmap = {fut: ("wv", 12, [1], "abc")}
    assert list(sample_prune.drain(futmap, consumed={fut})) == []


def test_drain_skips_a_genuinely_cancelled_future():
    """A cube that never started has nothing to bank and no proof was
    deleted."""
    fut = _cancelled()
    futmap = {fut: ("wv", 99, [1], "abc")}
    assert list(sample_prune.drain(futmap, consumed=set())) == []


def test_drain_surfaces_an_exception_instead_of_raising():
    """A worker that died during stand-down must not take the stand-down with
    it - the whole point is to salvage what is salvageable."""
    boom = RuntimeError("worker died")
    fut = _failed(boom)
    futmap = {fut: ("wv", 7, [1], "abc")}
    out = list(sample_prune.drain(futmap, consumed=set()))
    assert len(out) == 1
    job, row, err = out[0]
    assert row is None
    assert err is boom
    assert job[1] == 7


def test_drain_handles_a_mixed_batch():
    keep, seen, gone = _done(_row(1)), _done(_row(2)), _cancelled()
    futmap = {keep: ("wv", 1, [], ""), seen: ("wv", 2, [], ""),
              gone: ("wv", 3, [], "")}
    cubes = sorted(job[1] for job, _r, _e in
                   sample_prune.drain(futmap, consumed={seen}))
    assert cubes == [1], "only the unconsumed, uncancelled one"


# ------------------------------------------------------------ bank_row ---

def test_bank_row_puts_a_verified_check_in_the_transcript(tmp_path):
    trans, fails, checked = tmp_path / "t.jsonl", tmp_path / "f.jsonl", set()
    n = sample_prune.bank_row(_row(5), str(trans), str(fails), checked)
    assert n == 0
    assert not fails.exists(), "a verified proof must not touch CHECK_FAILURES"
    rec = json.loads(trans.read_text(encoding="ascii"))
    assert rec["cube"] == 5
    assert rec["sampled"] is True, "the sampled marker is part of the record"
    assert 5 in checked


def test_bank_row_puts_a_failed_check_in_check_failures(tmp_path):
    trans, fails, checked = tmp_path / "t.jsonl", tmp_path / "f.jsonl", set()
    n = sample_prune.bank_row(_row(6, ok=False), str(trans), str(fails), checked)
    assert n == 1, "a failure must be counted, or the halt never fires"
    assert not trans.exists()
    assert json.loads(fails.read_text(encoding="ascii"))["cube"] == 6


# ---------------------------------------------------------- stand_down ---

def test_stand_down_banks_the_inflight_rows(tmp_path):
    """The regression test. Before the fix these rows were dropped on the
    floor while their proofs had already been deleted."""
    trans, fails, checked = tmp_path / "t.jsonl", tmp_path / "f.jsonl", set()
    already = _done(_row(1))
    inflight_a, inflight_b = _done(_row(2)), _done(_row(3))
    futmap = {already: ("wv", 1, [], ""), inflight_a: ("wv", 2, [], ""),
              inflight_b: ("wv", 3, [], "")}
    ex = _FakeExecutor()

    nfail = sample_prune.stand_down(ex, futmap, {already}, str(tmp_path),
                                    str(trans), str(fails), checked)

    assert nfail == 0
    assert checked == {2, 3}, \
        "both in-flight results must be banked, not discarded"
    banked = [json.loads(x)["cube"]
              for x in trans.read_text(encoding="ascii").splitlines()]
    assert sorted(banked) == [2, 3]
    assert ex.calls == [{"wait": False, "cancel_futures": True}], \
        "it must still cancel what has not started, and not block on that"


def test_stand_down_still_records_a_failure_found_while_standing_down(tmp_path):
    """Standing down must not become a way to lose a bad proof either."""
    trans, fails, checked = tmp_path / "t.jsonl", tmp_path / "f.jsonl", set()
    futmap = {_done(_row(9, ok=False)): ("wv", 9, [], "")}
    nfail = sample_prune.stand_down(_FakeExecutor(), futmap, set(),
                                    str(tmp_path), str(trans), str(fails),
                                    checked)
    assert nfail == 1
    assert json.loads(fails.read_text(encoding="ascii"))["cube"] == 9


def test_stand_down_files_a_dead_worker_as_an_environment_fault(tmp_path):
    """CHECKER_ERRORS, never CHECK_FAILURES. That file halts the campaign and
    means the mathematics failed."""
    trans, fails, checked = tmp_path / "t.jsonl", tmp_path / "f.jsonl", set()
    futmap = {_failed(RuntimeError("pool died")): ("wv", 11, [], "")}
    nfail = sample_prune.stand_down(_FakeExecutor(), futmap, set(),
                                    str(tmp_path), str(trans), str(fails),
                                    checked)
    assert nfail == 0, "an infrastructure fault is not a check failure"
    assert not fails.exists(), \
        "a dead worker must NEVER reach CHECK_FAILURES.jsonl"
    err = tmp_path / "CHECKER_ERRORS.jsonl"
    assert err.exists(), "but it must be recorded, not swallowed"
    assert json.loads(err.read_text(encoding="ascii"))["cube"] == 11
