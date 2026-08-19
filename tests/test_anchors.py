"""The published anchors, and the one independent check available on them.

Spencer (1973): writing k = 2^t * m with m odd and t >= 1, N(k,2) = 2^t*(k-1)+1.
That covers every even k, which is eight of the fifteen published terms. A
transcription slip in ANCHORS.json shows up here rather than in a claim.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ANCHORS = json.loads((ROOT / "claims" / "ANCHORS.json").read_text(encoding="ascii"))


def parity_value(k: int) -> int:
    """N(k,2) for even k. Note the k-1, not m-1: M20 is exactly that slip."""
    if k % 2:
        raise ValueError(f"the parity formula covers even k only, got {k}")
    t = 0
    m = k
    while m % 2 == 0:
        m //= 2
        t += 1
    assert m % 2 == 1 and 2**t * m == k
    return 2**t * (k - 1) + 1


def test_anchor_file_shape():
    assert set(ANCHORS) == {"schema", "sequence", "offset", "terms", "source"}
    assert ANCHORS["schema"] == "nk2.anchors.v1"
    assert ANCHORS["sequence"] == "A398541"
    assert ANCHORS["offset"] == 2
    assert len(ANCHORS["terms"]) == 15
    assert all(isinstance(term, int) and term > 0 for term in ANCHORS["terms"])


@pytest.mark.parametrize("k", [2, 4, 6, 8, 10, 12, 14, 16])
def test_parity_formula_matches_every_even_anchor(k):
    assert parity_value(k) == ANCHORS["terms"][k - 2], k


def test_parity_formula_spot_values():
    # Guards the helper itself: M20 (using m-1 in place of k-1) gives 5 at k=6
    # and 3 at k=12, both of which differ from the published terms.
    assert parity_value(2) == 3
    assert parity_value(6) == 11
    assert parity_value(12) == 45
    assert parity_value(16) == 241


def test_anchors_are_strictly_indexed_from_the_offset():
    # a(k) sits at terms[k - offset]. An extensions index computed from 1 would
    # be silently off by one.
    terms = ANCHORS["terms"]
    assert terms[0] == 3  # a(2)
    assert terms[-1] == 241  # a(16)
    assert terms[3 - ANCHORS["offset"]] == 9  # a(3) = N(3,2) = 9


def test_gate_holds_the_same_anchors_as_a_literal():
    from gate.verify_all import ANCHOR_TERMS

    assert list(ANCHOR_TERMS) == ANCHORS["terms"]
