"""The 2026-08-22 17:03 outage: two zero-byte verdict files bricked everything.

A kill at 16:58 caught `solve_cube` between creating v12038.json and writing its
body. `json.load` on a zero-byte file raises JSONDecodeError, and EVERY consumer
of the verdicts directory called it bare:

    run_campaign.counts()   -> orchestrator dead 1 s after each restart
    sample_prune            -> pruner dead
    crash_cleanup           -> the tool whose whole job is clearing this debris
    cube_wave2.solve_cube   -> the wave driver itself

and the watchdog reported `RESTARTED ... (rc 0)` every 300 s, because the spawn
succeeded. It raises CAMPAIGN_ATTENTION only on a SAT verdict, a check failure
or DONE.json - a crash loop is none of those, so the Notepad alert would never
have fired and the campaign would have sat at 14,805/16,384 for days.

Two rules follow, and both are tested here:

  1. A verdict is written ATOMICALLY. A kill leaves either no file or a complete
     one - never a partial one. This is the root cause.
  2. An unreadable verdict is NEVER an exception and NEVER a result. It reads as
     "this cube has no verdict", so the cube is re-solved and the file is
     overwritten with a good one. The system self-heals.

Rule 2's direction is the safe one on purpose. Skipping an unreadable verdict
under-counts, which re-solves a cube that was already done - a few wasted
minutes. Counting it would let the wave declare 16,384/16,384 while a cube was
never actually decided, and that is a FALSE MATHEMATICAL RESULT.

Run: python -m pytest test_verdict_durability.py -q
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import verdict_io  # noqa: E402

GOOD = {"cube": 12039, "lits": [129, -133], "rc": 20, "wall_s": 901.62,
        "drat_sha256": "0942d1f6", "drat_bytes": 117937018}


# --------------------------------------------------------------------------
# Rule 2: an unreadable verdict is None, never an exception.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("body,label", [
    (b"", "zero-byte - the exact file that caused the outage"),
    (b'{"cube": 12038, "rc"', "truncated mid-key"),
    (b'{"cube": 12038, "rc": 2', "truncated mid-value"),
    (b"\x00\x00\x00\x00", "NUL bytes - what a lazy-written sparse file holds"),
    (b"[1, 2, 3]", "valid JSON but not an object"),
    (b'"just a string"', "valid JSON scalar"),
    (b"null", "valid JSON null"),
])
def test_unreadable_verdict_reads_as_none(tmp_path, body, label):
    p = tmp_path / "v12038.json"
    p.write_bytes(body)
    assert verdict_io.read_verdict(str(p)) is None, label


def test_missing_verdict_reads_as_none(tmp_path):
    assert verdict_io.read_verdict(str(tmp_path / "nope.json")) is None


def test_good_verdict_still_reads(tmp_path):
    p = tmp_path / "v12039.json"
    p.write_text(json.dumps(GOOD), encoding="ascii")
    assert verdict_io.read_verdict(str(p)) == GOOD


def test_verdict_without_required_keys_reads_as_none(tmp_path):
    """A dict is not enough. 'cube' and 'rc' are what every caller indexes,
    and cube_wave2.run_many does `v["wall_s"]` unguarded on the result."""
    p = tmp_path / "v1.json"
    p.write_text('{"wall_s": 12.0}', encoding="ascii")
    assert verdict_io.read_verdict(str(p)) is None


# --------------------------------------------------------------------------
# Rule 2 applied: counting must skip the bad file, not raise and not count it.
# --------------------------------------------------------------------------

def test_count_verdicts_skips_unreadable(tmp_path):
    verd = tmp_path / "verdicts"
    verd.mkdir()
    for i in range(5):
        (verd / f"v{i:05d}.json").write_text(
            json.dumps({"cube": i, "rc": 20}), encoding="ascii")
    (verd / "v00005.json").write_bytes(b"")          # the poison
    (verd / "v00006.json").write_text(
        json.dumps({"cube": 6, "rc": 10}), encoding="ascii")

    c = verdict_io.count_verdicts(str(verd))
    assert c["20"] == 5, "five real UNSAT"
    assert c["10"] == 1, "the SAT verdict must still be seen - it halts the run"
    assert c["unreadable"] == 1
    assert c["20"] != 6, "an unreadable verdict must NEVER be counted as UNSAT"


def test_count_verdicts_on_missing_dir_is_empty(tmp_path):
    c = verdict_io.count_verdicts(str(tmp_path / "nope"))
    assert c["20"] == 0 and c["unreadable"] == 0


# --------------------------------------------------------------------------
# Rule 1: the root cause. A crash mid-write must not leave a partial file.
# --------------------------------------------------------------------------

def test_atomic_write_leaves_no_partial_file_when_the_write_dies(tmp_path,
                                                                monkeypatch):
    """This is the outage, reproduced.

    The old code did `open(vpath, "w")` then `json.dump(...)`. The open alone
    creates a zero-byte file, so a kill in between leaves exactly the debris
    that halted the campaign. Atomic write must leave the real path untouched.
    """
    target = tmp_path / "v12038.json"

    def boom(*a, **k):
        raise KeyboardInterrupt("killed mid-write, as at 16:58")

    monkeypatch.setattr(verdict_io.json, "dump", boom)
    with pytest.raises(KeyboardInterrupt):
        verdict_io.write_verdict(str(target), GOOD)

    assert not target.exists(), (
        "a killed write left a file at the real path - this is the bug")
    # And it must clean up after itself. `except Exception` would miss this:
    # KeyboardInterrupt is a BaseException, and a kill is precisely the case
    # this guard exists for.
    assert [p.name for p in tmp_path.iterdir()] == [], \
        "the temp file was orphaned - the handler did not catch the kill"


def test_write_debris_is_ignored_by_every_iterator(tmp_path):
    """A real OS kill runs no handler at all, so orphaned .wv*.tmp files WILL
    appear in the verdicts directory. They live there because os.replace can
    only be atomic within a volume. Every iterator must skip them, or the fix
    reintroduces the outage wearing a different suffix."""
    verd = tmp_path / "verdicts"
    verd.mkdir()
    (verd / "v00001.json").write_text(json.dumps(GOOD), encoding="ascii")
    (verd / ".wv7h2k9.tmp").write_bytes(b'{"cube": 1, "rc"')   # orphaned
    (verd / "sample.json").write_text("[]", encoding="ascii")  # not a verdict

    names = [n for n, _ in verdict_io.iter_verdict_files(str(verd))]
    assert names == ["v00001.json"], f"iterator picked up non-verdicts: {names}"

    c = verdict_io.count_verdicts(str(verd))
    assert c["20"] == 1
    assert c["unreadable"] == 0, "write debris must not read as a poisoned verdict"


def test_atomic_write_then_read_roundtrips(tmp_path):
    target = tmp_path / "v12039.json"
    verdict_io.write_verdict(str(target), GOOD)
    assert verdict_io.read_verdict(str(target)) == GOOD


def test_atomic_write_overwrites_existing_debris(tmp_path):
    """Self-healing: re-solving a cube whose verdict was corrupt must replace
    it. os.replace overwrites on Windows; os.rename would raise."""
    target = tmp_path / "v12038.json"
    target.write_bytes(b"")
    verdict_io.write_verdict(str(target), GOOD)
    assert verdict_io.read_verdict(str(target)) == GOOD


def test_atomic_write_temp_file_is_cleaned_up_on_success(tmp_path):
    verdict_io.write_verdict(str(tmp_path / "v1.json"), GOOD)
    assert [p.name for p in tmp_path.iterdir()] == ["v1.json"]


# --------------------------------------------------------------------------
# End-to-end: the live tools must survive the poison file.
# --------------------------------------------------------------------------

def _fake_wave(tmp_path, n_good=3, poison=True):
    w = tmp_path / "wavefake"
    (w / "verdicts").mkdir(parents=True)
    (w / "drat").mkdir()
    for i in range(n_good):
        (w / "verdicts" / f"v{i:05d}.json").write_text(
            json.dumps({"cube": i, "rc": 20, "wall_s": 1.0,
                        "drat_sha256": "ab", "drat_bytes": 1}),
            encoding="ascii")
    if poison:
        (w / "verdicts" / "v09999.json").write_bytes(b"")
    return w


def test_crash_cleanup_survives_a_zero_byte_verdict(tmp_path):
    """crash_cleanup exists to clear exactly this debris, and it died on it."""
    w = _fake_wave(tmp_path)
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "crash_cleanup.py"), str(w)],
        capture_output=True, text=True, timeout=120, cwd=HERE)
    assert r.returncode == 0, f"crash_cleanup died: {r.stdout}\n{r.stderr}"
    assert "Traceback" not in r.stderr
    assert not (w / "verdicts" / "v09999.json").exists(), \
        "the unreadable verdict should have been cleared, so the cube re-solves"


def test_campaign_state_survives_a_zero_byte_verdict(tmp_path):
    """The sensor is the one thing a session reads to decide if all is well.
    A dead campaign and a dead sensor look identical from outside."""
    w = _fake_wave(tmp_path)
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "campaign_state.py"), str(w)],
        capture_output=True, text=True, timeout=180, cwd=HERE)
    assert "Traceback" not in r.stderr, r.stderr
    assert "STATE=" in r.stdout, r.stdout
