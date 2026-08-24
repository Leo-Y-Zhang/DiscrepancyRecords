"""Phase-1 ETA from CORE-HOURS SPENT, not from cube counts.

Cube counts lie here and the campaign has been misled by them twice. At 2,387
of 16,384 the wave was 14.6% through the CUBES but only 8.2% through the WORK:
the early cubes are cheap and the tail is heavy, so a verdict rate of 364/h
against a 206/h historical baseline looked like a 1.8x win and was not.

The honest method, and the one this prints:
  spent      = sum of wall_s over the verdicts on disk
  remaining  = 1262 core-hours (the measured cost of the whole wave, taken
               across all 16,384 cubes before the deletion) minus spent
  paralleism = core-seconds completed per wall-second, over a recent window
  eta        = remaining / parallelism

Prints one line, for a monitor.
"""
import json
import os
import time

V = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "wave274tot", "verdicts")
TOTAL_CORE_H = 1262.0     # whole-wave cost, measured pre-loss
SEQ_CORE_H = 511.0        # seqcount confirmation wave, 4096 cubes
WINDOW_S = 3 * 3600

rows = []
for n in os.listdir(V):
    if n.startswith("v") and n.endswith(".json"):
        p = os.path.join(V, n)
        try:
            d = json.load(open(p, encoding="ascii"))
        except Exception:
            continue
        if isinstance(d, dict) and "wall_s" in d:
            rows.append((os.path.getmtime(p), d["wall_s"]))

if not rows:
    print("no verdicts yet")
    raise SystemExit(0)

n = len(rows)
spent = sum(r[1] for r in rows) / 3600.0
now = time.time()
win = [r for r in rows if r[0] > now - WINDOW_S]

if len(win) > 30:
    span = max(r[0] for r in win) - min(r[0] for r in win)
    par = (sum(r[1] for r in win) / span) if span > 0 else 0.0
else:
    par = 0.0

if par > 0:
    rem_h = max(TOTAL_CORE_H - spent, 0.0) / par
    fin = time.strftime("%a %d %b %H:%M", time.localtime(now + rem_h * 3600))
    all_h = rem_h + SEQ_CORE_H / par
    finall = time.strftime("%a %d %b %H:%M", time.localtime(now + all_h * 3600))
    print(f"phase1 {n}/16384 cubes = {spent:.0f}/{TOTAL_CORE_H:.0f} core-h "
          f"({spent/TOTAL_CORE_H*100:.1f}% of WORK, {n/16384*100:.1f}% of cubes) "
          f"| {par:.1f}x | phase1 ends {fin} | campaign {finall}")
else:
    print(f"phase1 {n}/16384, {spent:.0f} core-h spent, window too small for a rate")
