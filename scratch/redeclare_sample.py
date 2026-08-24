"""Re-declare the proof sample, because the first declaration was not
independent of the order the wave solves cubes in.

THE DEFECT (found 2026-08-19 23:0x, after a Windows logoff killed the run and
the state was re-derived from disk). sample_prune.py drew the sample with
`random.Random(20260819).sample(range(16384), 1600)`. cube_wave2.py fixes the
processing order with `random.Random(20260819).shuffle(ids)` - the SAME seed.
For n=16384, k=1600 CPython takes random.sample's pool branch (setsize =
21 + 4**ceil(log(3k,4)) = 16405 >= n), which consumes exactly the same
_randbelow bound sequence as shuffle, so the two are the same partial
Fisher-Yates walk. Consequence, verified not argued:

    set(declared_sample) == set(shuffled_order[-1600:])   -> True
    overlap with the first 1600 cubes processed           -> 0

The "random sample" was precisely the LAST 1,600 cubes the wave would ever
reach - the earliest of them sits at processing position 14,784 of 16,384, so
no proof could have been checked until ~43 h into the wave, and the sample
could say nothing about the other 90% of the run. It is still a uniform subset
marginally, but it is perfectly correlated with run order, which is exactly
what a reader would assume it is not.

WHY RE-DECLARING NOW IS NOT CHERRY-PICKING: zero proofs from the old sample
had been checked (transcripts.jsonl was empty) and zero of its cubes had even
been solved (0 overlap with the 1,549 done). Nothing had been observed, so
nothing can be selected for. The old file is kept beside the new one.

THE POPULATION. The 1,549 cubes already solved had their proofs deleted with
digests recorded in pruned.jsonl, so they cannot be proof-checked by anyone
now. The new sample is therefore drawn from the cubes NOT yet solved, and the
declaration says so rather than implying a coverage it cannot have. Those
1,549 keep the guarantee every cube gets: solved twice, under two independent
encodings.
"""
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
W = os.path.join(HERE, "wave274tot")
TOTAL = 16384
SIZE = 1600
WAVE_SHUFFLE_SEED = 20260819      # cube_wave2.py - do not reuse
SAMPLE_SEED = 4177213             # independent of the wave's stream
ARCHIVE_EVERY = 512


def main():
    verd = os.path.join(W, "verdicts")
    solved = set()
    for f in os.listdir(verd):
        v = json.load(open(os.path.join(verd, f), encoding="ascii"))
        if v.get("rc") == 20:
            solved.add(v["cube"])
    eligible = sorted(set(range(TOTAL)) - solved)

    cubes = sorted(random.Random(SAMPLE_SEED).sample(eligible, SIZE))

    # The independence the old draw lacked, checked rather than assumed.
    order = list(range(TOTAL))
    random.Random(WAVE_SHUFFLE_SEED).shuffle(order)
    at = {c: i for i, c in enumerate(order)}
    pos = sorted(at[c] for c in cubes)
    eligible_set, sample_set = set(eligible), set(cubes)
    remaining_order = [c for c in order if c in eligible_set]
    bucket = -(-len(remaining_order) // 10)
    spread = [sum(1 for c in remaining_order[i:i + bucket] if c in sample_set)
              for i in range(0, len(remaining_order), bucket)]

    old = os.path.join(W, "sample.json")
    keep = os.path.join(W, "sample.superseded-v1.json")
    if os.path.exists(old) and not os.path.exists(keep):
        os.replace(old, keep)

    doc = {
        "schema": "proof-sample.v2",
        "seed": SAMPLE_SEED,
        "method": "random.Random(seed).sample(eligible, size), sorted",
        "population": "cube ids with no rc=20 verdict when this was declared",
        "n_cubes": TOTAL,
        "n_eligible": len(eligible),
        "n_already_solved_and_pruned": len(solved),
        "size": SIZE,
        "sample_rate_of_eligible": round(SIZE / len(eligible), 4),
        "archive_rule": f"proofs for cubes with id %% {ARCHIVE_EVERY} == 0 are "
                        f"retained on disk whether or not they are sampled",
        "supersedes": "sample.superseded-v1.json",
        "superseded_reason":
            "v1 was drawn with the same seed (20260819) as cube_wave2.py's "
            "processing shuffle; CPython's random.sample pool branch consumes "
            "the identical _randbelow stream as random.shuffle, so the v1 "
            "sample was exactly the last 1600 cubes of the processing order "
            "(verified: set equality with shuffled_order[-1600:], and zero "
            "overlap with the first 1600 processed). No v1 proof had been "
            "checked and no v1 cube had been solved when this replaced it.",
        "coverage_limit":
            f"{len(solved)} cubes were solved before this declaration and "
            f"their proofs are already deleted with sha256+size in "
            f"pruned.jsonl; they are not proof-checkable by anyone now. Every "
            f"cube, sampled or not, is still solved twice under two "
            f"independent encodings - that, not this sample, is the per-cube "
            f"guarantee. This sample tests the proof pipeline.",
        "independence_check": {
            "earliest_processing_position": pos[0],
            "latest_processing_position": pos[-1],
            "per_decile_of_remaining_work": spread,
        },
        "cubes": cubes,
    }
    with open(os.path.join(W, "sample.json"), "w", encoding="ascii",
              newline="\n") as fh:
        json.dump(doc, fh)

    print(f"eligible (unsolved) cubes : {len(eligible)}")
    print(f"already solved and pruned : {len(solved)}")
    print(f"new sample size           : {len(cubes)} "
          f"({SIZE/len(eligible)*100:.1f}% of eligible)")
    print(f"processing positions      : {pos[0]} .. {pos[-1]} of {TOTAL}")
    print(f"sampled per decile of the remaining work: {spread}")
    print(f"old declaration preserved : {os.path.basename(keep)}")


if __name__ == "__main__":
    raise SystemExit(main())
