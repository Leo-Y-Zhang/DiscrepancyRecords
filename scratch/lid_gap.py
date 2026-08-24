"""Did the solver keep working while the lid was shut?

Bucket verdict-file mtimes into one-minute bins over the last 30 minutes. A
sleep shows up as a run of empty minutes; continuous work shows a flat rate.
"""
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERD = os.path.join(HERE, "wave274tot", "verdicts")

now = time.time()
times = []
for f in os.listdir(VERD):
    m = os.path.getmtime(os.path.join(VERD, f))
    if now - m < 1800:
        times.append(m)
times.sort()
print(f"total verdicts: {len(os.listdir(VERD))}, in last 30 min: {len(times)}")
bins = {}
for t in times:
    minutes_ago = int((now - t) // 60)
    bins[minutes_ago] = bins.get(minutes_ago, 0) + 1
print("minutes-ago : cubes finished in that minute")
for m in range(29, -1, -1):
    n = bins.get(m, 0)
    clock = time.strftime("%H:%M", time.localtime(now - m * 60))
    bar = "#" * min(n, 60)
    print(f"{clock} (-{m:2d}m): {n:3d} {bar}")
