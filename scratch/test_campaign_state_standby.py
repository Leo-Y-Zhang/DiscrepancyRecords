"""Tests for campaign_state.standby_minutes - the Modern Standby detector.

Run: python test_campaign_state_standby.py

⚠ This sensor reported 23.8h against a true 8.85h on its first run. The error
was invisible in the output - the number simply looked plausible. It was caught
only because the true value had been derived BY HAND from the raw event log
first. So every case here pins a number computed independently of the code, and
the ordering case is MUTATION-CHECKED: the naive sort must be observed FAILING.
"""
import io
import sys
import time
import types

import campaign_state as cs

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      got={got!r}\n      want={want!r}")
    if not ok:
        FAILURES.append(name)


def fake_events(lines):
    """Patch the powershell call to return canned Kernel-Power rows."""
    def run(*a, **k):
        return types.SimpleNamespace(stdout="\n".join(lines), stderr="", returncode=0)
    cs.subprocess.run = run


def rel(hours_ago):
    """An ISO local timestamp N hours before now, matching what the PS emits."""
    return time.strftime("%Y-%m-%dT%H:%M:%S",
                         time.localtime(time.time() - hours_ago * 3600))


real_run = cs.subprocess.run

# --- Case A: the real 2026-08-21 shape ------------------------------------
# A lid close at T-9h, two same-second exit/enter transitions as Windows
# re-decides the reason, one exit at T-0.15h. One continuous sleep of 8.85h.
# Hand-derived: (9 - 0.15) * 60 = 531.0 minutes, and the reason that STARTED
# it is Lid - not the "16777220" the last transition reports.
# The 4th field is RecordId - monotonic in the event log, and the ONLY thing
# that knows the order of two events inside the same second.
A = [
    f"{rel(9.0)}|506|Lid|101",
    f"{rel(8.98)}|507|AC/DC Display Burst Suppressed|102",
    f"{rel(8.98)}|506|AC/DC Display Burst Suppressed|103",
    f"{rel(8.7)}|507|16777220|104",
    f"{rel(8.7)}|506|16777220|105",
    f"{rel(0.15)}|507|Lid|106",
]
fake_events(A)
mins, why = cs.standby_minutes(24)
check("A: total minutes for one 8.85h lid sleep", round(mins), 531)
check("A: reason is what STARTED it, not the last transition", why, "Lid")

# --- Case A2: the real 2026-08-22 shape, and it is the OPPOSITE order --------
# 17:02:13 id=506 "Sleep, Hibernate, or Shutdown" then 17:02:13 id=507 "Power
# Button" - the box slept and the power button woke it inside one second.
# Hand-derived from the raw log: 0 minutes. THE SENSOR REPORTED 24.0h.
#
# ⚠⚠ This is why the 21 Aug rule was wrong as a GENERAL rule: it assumed a
# same-second pair is always exit-then-enter. Case A and Case A2 are both real
# and are opposite orders, so NO rule based on the event id can serve both.
A2 = [
    f"{rel(0.5)}|506|Sleep, Hibernate, or Shutdown|201",
    f"{rel(0.5)}|507|Power Button|202",
]
fake_events(A2)
mins, why = cs.standby_minutes(24)
check("A2: a one-second sleep is ~0 min, NOT the whole window", round(mins), 0)

# --- Case B: MUTATIONS - both orderings must be observed FAILING -------------
# If either mutant still returns the right answer, the case above is decoration.
src = open(cs.__file__, encoding="utf-8").read()
assert "rows.sort(key=lambda r: (r[0], r[3]))" in src, \
    "the RecordId ordering fix is not in the source - test is aimed at nothing"


def mutant(new_sort):
    mutated = src.replace("rows.sort(key=lambda r: (r[0], r[3]))", new_sort)
    assert mutated != src, "mutation did not apply"
    mod = types.ModuleType("cs_mutated")
    mod.__file__ = cs.__file__
    exec(compile(mutated, cs.__file__, "exec"), mod.__dict__)
    mod.subprocess.run = cs.subprocess.run       # same canned events
    return mod


fake_events(A)
m, w = mutant("rows.sort()").standby_minutes(24)
ok = round(m) != 531
print(f"{'PASS' if ok else 'FAIL'}  B1: naive sort OBSERVED FAILING on the 21 Aug"
      f" shape\n      mutant reported {m:.0f} min (correct 531)")
if not ok:
    FAILURES.append("B1: mutation not observed failing")

fake_events(A2)
m, w = mutant('rows.sort(key=lambda r: (r[0], 0 if r[1] == "507" else 1))'
              ).standby_minutes(24)
ok = round(m) != 0
print(f"{'PASS' if ok else 'FAIL'}  B2: the 21 Aug id-based rule OBSERVED FAILING"
      f" on the 22 Aug shape\n      mutant reported {m:.0f} min (correct 0)"
      f" - this is the 24.0h false alarm")
if not ok:
    FAILURES.append("B2: mutation not observed failing")

# --- Case C: standby began BEFORE the window --------------------------------
# Only an exit is visible. It must clamp to the window start, not be dropped
# (dropping it would under-report a sleep that is still costing throughput).
fake_events([f"{rel(2.0)}|507|Lid"])
mins, why = cs.standby_minutes(24)
check("C: unmatched exit clamps to window start", round(mins), round((24 - 2) * 60))

# --- Case D: still asleep at the end of the window ---------------------------
fake_events([f"{rel(3.0)}|506|Lid"])
mins, why = cs.standby_minutes(24)
check("D: unmatched enter runs to now", round(mins), 180)
check("D: reason reported", why, "Lid")

# --- Case E: two SEPARATE sleeps must not merge ------------------------------
# 30 min apart is a real wake, not a same-second transition. 1h + 1h = 120 min.
fake_events([
    f"{rel(6.0)}|506|Idle Timeout",
    f"{rel(5.0)}|507|Input Mouse",
    f"{rel(4.5)}|506|Lid",
    f"{rel(3.5)}|507|Lid",
])
mins, why = cs.standby_minutes(24)
check("E: distinct sleeps summed, not merged", round(mins), 120)

# --- Case F: quiet box -------------------------------------------------------
fake_events([])
check("F: no events", cs.standby_minutes(24), (0.0, "none"))

# --- Case G: the standby probe dying must not take the sensor down -----------
def run_main_captured():
    buf, real_stdout = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        cs.main()
    finally:
        sys.stdout = real_stdout
    return buf.getvalue()


def boom_on_standby(*a, **k):
    cmd = a[0] if a else k.get("args")
    if any("Kernel-Power" in str(x) for x in cmd):
        raise OSError("powershell is gone")
    return real_run(*a, **k)


cs.subprocess.run = boom_on_standby
out = run_main_captured()
check("G: sensor still reports STATE when the standby probe dies",
      ("STATE=" in out) and ("standby_24h=UNKNOWN" in out), True)

# --- Case H: powershell entirely unavailable ---------------------------------
# ⚠ Found BY case G's own first failure: procs() launched powershell unguarded,
# so this killed the whole sensor with a traceback. An unattended poll would
# have returned nothing at all - and a dead campaign and a dead sensor look
# exactly alike from the outside. Now it degrades to procs=-1 and still reports.
def boom_always(*a, **k):
    raise OSError("powershell is gone")


cs.subprocess.run = boom_always
out = run_main_captured()
check("H: sensor survives powershell being gone entirely",
      ("STATE=" in out) and ("orchestrator=-1" in out)
      and ("standby_24h=UNKNOWN" in out), True)

cs.subprocess.run = real_run
print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    raise SystemExit(1)
print("all standby-detector cases pass")
