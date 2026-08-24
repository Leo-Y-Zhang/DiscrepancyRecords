"""Durable, poison-proof access to a wave's verdicts directory.

Written 2026-08-22 after two zero-byte verdict files took the whole campaign
down. A kill at 16:58 caught `solve_cube` between `open(vpath, "w")` - which
creates the file - and `json.dump` - which fills it. Every consumer of the
directory then called `json.load` bare, so the orchestrator, the pruner, the
wave driver and `crash_cleanup` itself all died on the same two files, one
second after each of eight restarts. The watchdog logged `RESTARTED ... (rc 0)`
throughout, because spawning had in fact succeeded, and it raises the Notepad
alert only on a SAT verdict, a check failure or DONE.json. A crash loop is none
of those. Left alone it would have run silently for days at 14,805/16,384.

Two rules, and the second one has a direction that matters:

  1. WRITE ATOMICALLY. A kill leaves either no file or a complete file.
  2. AN UNREADABLE VERDICT IS NOT A RESULT AND NOT AN EXCEPTION. It reads as
     "this cube has no verdict", so the driver re-solves the cube and overwrites
     the file. The system heals itself.

Rule 2 skips rather than counts on purpose. Skipping under-counts, which
re-solves a cube that was already decided - a few minutes wasted. Counting an
unreadable file would let the wave report 16,384/16,384 with a cube that was
never actually decided, and that is a false mathematical result. When the two
failure directions are not equal, take the expensive one.

Tests: test_verdict_durability.py
"""
import json
import os
import tempfile

__all__ = ["read_verdict", "write_verdict", "iter_verdict_files",
           "count_verdicts", "is_verdict_name"]

#: Keys every caller indexes without checking. `run_many` does v["wall_s"],
#: `counts` does v["rc"], the importer does v["cube"]. A dict missing these is
#: as useless as an unparseable file, so it is treated the same way.
REQUIRED = ("cube", "rc")


def is_verdict_name(name):
    """True for a real verdict file.

    Excludes the `.tmp` files write_verdict leaves behind if it is killed
    mid-write. Those live in the same directory - they have to, an atomic
    replace cannot cross a volume - so every iterator must filter them out or
    the fix reintroduces the bug in a new costume.
    """
    return name.startswith("v") and name.endswith(".json")


def read_verdict(path):
    """Return the verdict dict, or None if it cannot be trusted.

    None covers: absent, unreadable, empty, truncated, not JSON, JSON that is
    not an object, and an object missing `cube` or `rc`. Callers treat None as
    "no verdict for this cube".
    """
    try:
        with open(path, encoding="ascii") as fh:
            v = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        # ValueError covers JSONDecodeError. Deliberately NOT bare except:
        # a KeyboardInterrupt or MemoryError here is a real problem and must
        # not be silently turned into "no verdict".
        return None
    if not isinstance(v, dict):
        return None
    if any(k not in v for k in REQUIRED):
        return None
    return v


def write_verdict(path, verdict):
    """Write a verdict so that a kill can never leave a partial file.

    Writes to a temp file in the SAME directory (os.replace is only atomic
    within a volume), fsyncs it, then replaces the target in one step.
    os.replace, not os.rename: on Windows rename raises if the target exists,
    and the target does exist whenever a corrupt verdict is being healed.
    """
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".wv", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as fh:
            json.dump(verdict, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # BaseException, because the failure this guards against is a kill -
        # and KeyboardInterrupt is not an Exception. Leave the real path alone.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def iter_verdict_files(verd):
    """Yield (name, full_path) for every real verdict file, sorted."""
    if not os.path.isdir(verd):
        return
    for name in sorted(os.listdir(verd)):
        if is_verdict_name(name):
            yield name, os.path.join(verd, name)


def count_verdicts(verd):
    """Count verdicts by rc. Unreadable files are counted separately and are
    never folded into a result bucket."""
    c = {"10": 0, "20": 0, "other": 0, "unreadable": 0}
    for _, path in iter_verdict_files(verd):
        v = read_verdict(path)
        if v is None:
            c["unreadable"] += 1
            continue
        k = str(v.get("rc"))
        c[k if k in c else "other"] += 1
    return c
