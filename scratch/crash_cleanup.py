"""Clear what the 2026-08-19 22:46 Windows logoff left behind in wave274tot.

The logoff killed every process in the session. kissat processes that were
mid-solve exited with 0xC000013A (STATUS_CONTROL_C_EXIT = 3221225786), and
solve_cube dutifully wrote that as a verdict. Those verdicts are not results:
rc is neither 10 nor 20, so the driver re-solves the cube on resume anyway -
deleting them just stops any counter from having to interpret 3221225786.

Their .drat files are truncated mid-write and must go with them.

Separately: gzip_loop.py used to delete each cube's .cnf once its proof was
compressed, and it was retired (compressing a proof about to be deleted was
waste). Nothing took over that job, so cube CNFs now leak at ~16 MB each -
260 GB over a full wave. This clears the ones already stranded; sample_prune.py
takes over the job from here.

Usage: crash_cleanup.py [wavedir]   (default wave274tot)

The seqcount wave carries its own version of this: the 19 Aug run that was
killed externally left rc 1 and rc 4294967295 verdicts among its 117, which the
evidence importer names one by one as "only rc 20 is UNSAT".
"""
import os
import sys

import verdict_io

HERE = os.path.dirname(os.path.abspath(__file__))
WAVE = sys.argv[1] if len(sys.argv) > 1 else "wave274tot"
W = os.path.join(HERE, WAVE)
VERD, DRAT = os.path.join(W, "verdicts"), os.path.join(W, "drat")


def main():
    killed, unsat = [], set()
    for f, path in verdict_io.iter_verdict_files(VERD):
        v = verdict_io.read_verdict(path)
        if v is None:
            # 2026-08-22: a kill mid-write leaves a zero-byte verdict, and
            # json.load on it raised right here - in the one tool whose job is
            # clearing this exact debris. It is debris like any other: take the
            # cube id from the FILENAME, since the body is what is unreadable.
            try:
                cube = int(f[1:-len(".json")])
            except ValueError:
                continue
            killed.append((cube, f, "unreadable"))
        elif v.get("rc") == 20:
            unsat.add(v["cube"])
        elif v.get("rc") not in (10, 20):
            killed.append((v["cube"], f, v.get("rc")))

    freed = 0
    for cube, fname, _rc in killed:
        os.remove(os.path.join(VERD, fname))
        for junk in (os.path.join(DRAT, f"cube_{cube:05d}.drat"),
                     os.path.join(DRAT, f"cube_{cube:05d}.drat.gz"),
                     os.path.join(W, f"cube_{cube:05d}.cnf")):
            if os.path.exists(junk):
                freed += os.path.getsize(junk)
                os.remove(junk)
    seen = sorted({rc for _, _, rc in killed}, key=repr)
    print(f"cleared {len(killed)} non-verdict(s) from {WAVE} and their partial "
          f"proofs; return codes seen: {seen}")

    stale = 0
    for f in os.listdir(W):
        if not (f.startswith("cube_") and f.endswith(".cnf")):
            continue
        cube = int(f[len("cube_"):-len(".cnf")])
        if cube in unsat:
            p = os.path.join(W, f)
            freed += os.path.getsize(p)
            os.remove(p)
            stale += 1
    print(f"deleted {stale} stranded cube CNFs (regenerable from base + units)")
    print(f"reclaimed {freed/1e9:.2f} GB")
    print(f"cubes still UNSAT and banked: {len(unsat)}")


if __name__ == "__main__":
    raise SystemExit(main())
