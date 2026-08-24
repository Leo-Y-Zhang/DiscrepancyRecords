"""Did Modern Standby stop the wave, or only slow it?

The system entered Modern Standby at 2026-08-20 23:53:44 (reason: Idle Timeout)
and left it at 03:07:09 (reason: Lid). Verdict file mtimes are the only honest
witness to what the workers did in between - the driver's own progress line
cannot be trusted for rates.

Prints a per-10-minute histogram across the standby window.
"""
import os
import time

W = r"C:\dev\DiscrepancyRecords\scratch\wave274tot\verdicts"

ts = sorted(os.path.getmtime(os.path.join(W, f)) for f in os.listdir(W))


def at(s):
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


start, end = at("2026-08-19 23:30:00"), at("2026-08-20 03:20:00")
print("verdicts written per 10 min, 23:30 -> 03:20")
print("(standby 23:53:44 -> 03:07:09)")
gap_total = 0
t = start
while t < end:
    n = sum(1 for x in ts if t <= x < t + 600)
    label = time.strftime("%H:%M", time.localtime(t))
    mark = ""
    if at("2026-08-19 23:53:44") <= t < at("2026-08-20 03:07:09"):
        mark = "  <- in standby"
        gap_total += n
    print(f"  {label}  {n:>4} {'#' * min(n, 60)}{mark}")
    t += 600

print()
print(f"verdicts completed DURING standby: {gap_total}")
last = max(ts)
print(f"last verdict written: {time.strftime('%H:%M:%S', time.localtime(last))}")
