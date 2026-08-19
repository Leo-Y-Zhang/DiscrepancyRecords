"""The evaluator is the arithmetic of record. Everything else is checked against it."""

import pytest

from nk2.evaluator import avoids, first_bad_ap, max_abs_ap_sum


def coloring(s: str) -> list[int]:
    return [1 if c == "+" else -1 for c in s]


def periodic_k17_n272() -> list[int]:
    """The published N(17,2) >= 273 witness: nine plus, eight minus, tiled 16 times."""
    return coloring(("+" * 9 + "-" * 8) * 16)


def test_eight_term_coloring_avoids_3_2():
    # --++--++ has no monochromatic 3-AP, so N(3,2) >= 9.
    f = coloring("--++--++")
    assert len(f) == 8
    assert avoids(f, 3, 2)
    assert first_bad_ap(f, 3, 2) is None
    assert max_abs_ap_sum(f, 3)[0] == 1


def test_published_k17_n272_witness_avoids():
    # An independent re-check of the cited lower bound: every 17-AP inside
    # {1..272} has 1 <= d <= 16, so it meets every residue class mod 17 exactly
    # once and sums to 9 - 8 = 1.
    f = periodic_k17_n272()
    assert len(f) == 272
    assert avoids(f, 17, 2)
    best, ap = max_abs_ap_sum(f, 17)
    assert best == 1
    assert ap is not None


def test_one_more_term_of_the_same_period_does_not_help():
    # At N = 273 the AP 1, 18, ..., 273 has d = 17 and is monochromatic-by-residue,
    # so the naive extension of the witness fails. This is why a(17) is open.
    f = periodic_k17_n272() + [1]
    assert len(f) == 273
    assert not avoids(f, 17, 2)


def test_sign_flip_is_symmetric():
    f = coloring("--++--++")
    assert avoids([-v for v in f], 3, 2)
    g = coloring("+++")
    assert not avoids(g, 3, 2)
    assert not avoids([-v for v in g], 3, 2)


def test_sum_equal_to_l_is_not_avoided():
    # M1: relaxing `>= l` to `> l` would call these avoiding. Sums of an even
    # number of +-1 values are even, so |sum| == l is reachable and load-bearing.
    assert not avoids([1, 1], 2, 2)
    assert not avoids([-1, -1], 2, 2)
    assert avoids([1, -1], 2, 2)
    assert not avoids([1, 1, -1, 1], 4, 2)


def test_violation_needing_d_at_least_two():
    # M2 (only d = 1) misses this: the only bad AP has d = 2.
    f = coloring("+-+-+")
    assert first_bad_ap(f, 3, 2) == (1, 3, 5)
    assert not avoids(f, 3, 2)


def test_violation_at_maximal_a():
    # M3 (dropping the last a of each d) misses this: the only bad AP is
    # (7, 8, 9), the largest a at d = 1.
    f = coloring("+-+-+-+++")
    assert first_bad_ap(f, 3, 2) == (7, 8, 9)


def test_first_bad_ap_is_first_in_canonical_order():
    f = coloring("+++++")
    assert first_bad_ap(f, 3, 2) == (1, 2, 3)


def test_no_aps_means_vacuous_avoidance():
    f = coloring("++")
    assert avoids(f, 3, 2)
    assert max_abs_ap_sum(f, 3) == (0, None)


def test_l_one_and_odd_k_is_unavoidable():
    # Sums of an odd number of +-1 values are odd, so |sum| >= 1 always: N(k,1) = k.
    for f in ([1, 1, 1], [1, -1, 1], [-1, -1, 1]):
        assert not avoids(f, 3, 1)
    # Even k can reach sum 0, so l = 1 is avoidable there.
    assert avoids([1, -1], 2, 1)


def test_rejects_values_that_are_not_plus_or_minus_one():
    for bad in ([1, 0, -1], [1, 2], [1, "+"], [1, True], [1, 1.0]):
        with pytest.raises((ValueError, TypeError)):
            avoids(bad, 3, 2)


def test_rejects_bad_k_and_l():
    with pytest.raises(ValueError):
        avoids([1, -1, 1], 1, 2)
    with pytest.raises(ValueError):
        avoids([1, -1, 1], 3, 0)
    with pytest.raises(TypeError):
        avoids([1, -1, 1], 3, 2.0)
