"""One-line campaign state for an unattended poller.

Prints a single STATE token followed by the numbers behind it, so a scheduled
check costs one command and a short read rather than a fresh investigation
every few hours.

STATE is one of:
  ATTENTION   the watchdog hit a terminal condition and stopped for a REASON
  DONE        DONE.json exists - the compute is finished, harvest it
  WAVE_DONE   all 16,384 totalizer cubes UNSAT; phase 3/4 still to run
  SAT         a cube came back SAT - a(17)=274 is in question, needs a person
  DEAD        nothing is running and no terminal condition explains it
  RUNNING     normal

Usage: campaign_state.py
"""
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
W1 = os.path.join(HERE, "wave274tot")
W2 = os.path.join(HERE, "wave274")
TOT1, TOT2 = 16384, 4096


def verdict_counts(wavedir):
    d = os.path.join(wavedir, "verdicts")
    c = {"unsat": 0, "sat": 0, "other": 0, "newest": 0.0}
    if not os.path.isdir(d):
        return c
    for f in os.listdir(d):
        p = os.path.join(d, f)
        c["newest"] = max(c["newest"], os.path.getmtime(p))
        try:
            rc = json.load(open(p, encoding="ascii")).get("rc")
        except Exception:  # noqa: BLE001
            c["other"] += 1
            continue
        c["unsat" if rc == 20 else "sat" if rc == 10 else "other"] += 1
    return c


def procs():
    """Count by PROCESS, never by a log line. A log can look healthy for hours
    after the thing writing it has died - that is how this campaign was found
    dead at 03:09 on 20 Aug."""
    # ⚠ The launch itself must be guarded, not just the parse. Unguarded, a
    # powershell that will not start takes the WHOLE sensor down with a
    # traceback - so an unattended poll returns nothing at all instead of
    # "procs=-1", and a dead campaign and a dead sensor look identical.
    dead = dict(orch=-1, kissat=-1, checker=-1, pruner=-1, watchdog=-1)
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "$p=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\");"
             "$o=@($p|Where-Object{$_.CommandLine -like '*run_campaign.py*'}).Count;"
             "$c=@($p|Where-Object{$_.CommandLine -like '*sample_prune.py*' -and "
             "$_.CommandLine -like '*1 check*'}).Count;"
             "$r=@($p|Where-Object{$_.CommandLine -like '*sample_prune.py*' -and "
             "$_.CommandLine -like '*1 prune*'}).Count;"
             "$k=@(Get-Process kissat -ErrorAction SilentlyContinue).Count;"
             # ⚠ The watchdog is watchdog.PY on python.exe since 2026-08-20 - it
             # had to be ported because PowerShell will not run without a console,
             # and having no console is the whole fix. Left pointed at
             # powershell.exe this reported watchdog=0 beside a perfectly healthy
             # watchdog: a stick that reads "dead" forever is as useless as one
             # that reads "alive". -ne $PID is NOT optional: this very command
             # line contains the string "watchdog.py", so the querying process
             # matches its own filter. It read 2 watchdogs when there was 1 - and
             # worse, it would read 1 when there were NONE, turning a dead
             # watchdog into a healthy poll.
             "$w=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\"|"
             "Where-Object{$_.CommandLine -like '*watchdog.py*' -and "
             "$_.ProcessId -ne $PID}).Count;"
             "\"$o $k $c $r $w\""],
            capture_output=True, text=True)
    except Exception:  # noqa: BLE001
        return dead
    try:
        o, k, c, r, w = (int(x) for x in out.stdout.split())
    except Exception:  # noqa: BLE001
        return dead
    return dict(orch=o, kissat=k, checker=c, pruner=r, watchdog=w)


def standby_minutes(hours=24):
    """Minutes spent in Modern Standby recently, and why it last went in.

    ⚠⚠ THIS EXISTS BECAUSE A LID CLOSE COST 8h51m ON 2026-08-21 AND NOTHING
    NOTICED. Modern Standby does not STOP the wave, it HALVES it (~204/h awake
    vs ~125/h asleep), so it leaves NO GAP in verdict production - and the
    2026-08-19 test that certified "the lid may be closed" was a per-minute
    histogram looking for exactly that gap. It passed, and it was wrong.
    A stick that measures the wrong quantity reads PASS with total confidence.

    Returns (minutes, reason_of_last_entry) or (None, "") if it cannot tell.
    NEVER raises: a broken sensor line must not take the whole sensor down.
    """
    # ⚠ Concatenated, NOT f-string/.format(): the PowerShell body is full of
    # literal braces (@{LogName=...}, the -f placeholders {0}|{1}|{2}) and any
    # brace-based formatter would either mangle them or need them doubled.
    ps = (
        "$s=(Get-Date).AddHours(-" + str(int(hours)) + ");"
        "$e=Get-WinEvent -FilterHashtable @{LogName='System';"
        "ProviderName='Microsoft-Windows-Kernel-Power';Id=506,507;StartTime=$s}"
        " -ErrorAction SilentlyContinue;"
        "foreach($x in $e){$m=($x.Message -replace '\\s+',' ');$r='?';"
        "if($m -match 'Reason: ([^.]+)'){$r=$matches[1]};"
        "\"{0}|{1}|{2}|{3}\" -f $x.TimeCreated.ToString('yyyy-MM-ddTHH:mm:ss'),"
        "$x.Id,$r,$x.RecordId}"
    )
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True)
    rows = []
    for ln in out.stdout.splitlines():
        parts = ln.strip().split("|")
        if len(parts) != 3:
            continue
        try:
            t = time.mktime(time.strptime(parts[0], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
        rows.append((t, parts[1].strip(), parts[2].strip()))
    if not rows:
        return (0.0, "none")
    # ⚠⚠ AT A SAME-SECOND TRANSITION THE EXIT COMES FIRST, THEN THE ENTER.
    # Sorting naively puts "506" before "507" alphabetically, which flips them,
    # leaves the final exit unmatched, sends it down the clamp-to-window-start
    # branch and reported 23.8h of standby against a true 8.85h. Caught only
    # because the true value had been derived BY HAND from the raw events first.
    rows.sort(key=lambda r: (r[0], 0 if r[1] == "507" else 1))
    now = time.time()
    window_start = now - hours * 3600
    spans, enter, reason = [], None, ""
    # An exit with no matching enter means standby began before the window -
    # clamp to the window start rather than dropping the interval silently.
    for t, eid, r in rows:
        if eid == "506":
            if enter is None:
                enter, reason = t, r
        elif eid == "507":
            spans.append([enter if enter is not None else window_start, t, reason])
            enter, reason = None, ""
    if enter is not None:            # still asleep at the end of the window
        spans.append([enter, now, reason])

    # ⚠ MERGE spans separated by a momentary wake. Windows exits and re-enters
    # Modern Standby in the SAME SECOND while re-deciding the reason, so one
    # 8h51m lid sleep arrives as three spans reading Lid -> "AC/DC Display
    # Burst Suppressed" -> "16777220". Reporting the LAST reason would tell the
    # next session "16777220"; the one that matters is what STARTED it: Lid.
    merged = []
    for s in sorted(spans):
        if merged and s[0] - merged[-1][1] < 60:
            merged[-1][1] = max(merged[-1][1], s[1])
        else:
            merged.append(list(s))
    if not merged:
        return (0.0, "none")
    total = sum(e - b for b, e, _ in merged)
    longest = max(merged, key=lambda s: s[1] - s[0])
    return (max(0.0, total) / 60.0, longest[2] or "?")


def main():
    attention = os.path.join(HERE, "CAMPAIGN_ATTENTION.txt")
    done = os.path.join(HERE, "DONE.json")
    c1, c2 = verdict_counts(W1), verdict_counts(W2)
    p = procs()

    trans = os.path.join(W1, "transcripts.jsonl")
    nchecked = 0
    if os.path.exists(trans):
        with open(trans, encoding="ascii") as fh:
            nchecked = sum(1 for ln in fh if ln.strip())

    stale_min = (time.time() - c1["newest"]) / 60 if c1["newest"] else 9999

    if c1["sat"] or c2["sat"]:
        state = "SAT"
    elif os.path.exists(attention):
        state = "ATTENTION"
    elif os.path.exists(done):
        state = "DONE"
    elif p["orch"] == 0 and p["kissat"] == 0:
        state = "DEAD"
    elif c1["unsat"] >= TOT1:
        state = "WAVE_DONE"
    else:
        state = "RUNNING"

    print(f"STATE={state}")
    print(f"wave1={c1['unsat']}/{TOT1} unsat  sat={c1['sat']}  "
          f"other={c1['other']}  last_verdict={stale_min:.0f}min ago")
    print(f"wave2={c2['unsat']}/{TOT2} unsat  sat={c2['sat']}")
    print(f"proofs_verified={nchecked}/1600")
    print(f"procs: orchestrator={p['orch']} kissat={p['kissat']} "
          f"checker={p['checker']} pruner={p['pruner']} watchdog={p['watchdog']}")
    try:
        mins, why = standby_minutes(24)
        if mins is None:
            print("standby_24h=UNKNOWN")
        else:
            flag = "  <-- COSTING ~37% OF THE WAVE" if mins > 30 else ""
            print(f"standby_24h={mins/60:.1f}h ({mins:.0f} min)  "
                  f"last_entry={why}{flag}")
    except Exception as exc:  # noqa: BLE001
        print(f"standby_24h=UNKNOWN ({type(exc).__name__})")
    for f, label in ((attention, "ATTENTION"), (done, "DONE")):
        if os.path.exists(f):
            print(f"--- {label} ---")
            print(open(f, encoding="ascii", errors="replace").read().strip()[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
