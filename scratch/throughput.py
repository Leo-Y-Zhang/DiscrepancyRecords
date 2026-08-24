"""Measure both waves' completion rates from verdict mtimes, in 10-minute
buckets, so the effect of running them concurrently is visible rather than
assumed. Usage: throughput.py [minutes_back]"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WAVES = (("wave1 totalizer", "wave274tot", 16384),
         ("wave2 seqcount ", "wave274", 4096))


def main():
    back = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    now = time.time()
    print(f"{'bucket':>7} " + " ".join(f"{n:>16}" for n, _, _ in WAVES))
    per = {}
    for name, d, total in WAVES:
        verd = os.path.join(HERE, d, "verdicts")
        ts = [os.path.getmtime(os.path.join(verd, f)) for f in os.listdir(verd)]
        per[name] = (ts, len(ts), total)
    for start in range(back, 0, -10):
        row = f"-{start:>3}min "
        for name, _, _ in WAVES:
            ts, _, _ = per[name]
            n = sum(1 for t in ts if start * 60 >= now - t > (start - 10) * 60)
            row += f"{n:>10} ({n*6:>3}/h)"
        print(row)
    print()
    for name, _, _ in WAVES:
        ts, done, total = per[name]
        recent = sum(1 for t in ts if now - t < 1800)
        rate = recent * 2
        left = (total - done) / rate if rate else float("inf")
        print(f"{name}: {done}/{total} | {rate}/h | ~{left:.0f}h left"
              if rate else f"{name}: {done}/{total} | no completions in 30 min")


if __name__ == "__main__":
    main()
