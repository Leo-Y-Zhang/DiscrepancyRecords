"""The threshold u and the avoidance window it defines."""

import pytest

from nk2.evaluator import avoids
from nk2.spec import avoid_bounds, u_threshold


def test_u_is_integer_ceiling_not_floor():
    # M4: ceil -> floor. These are the odd (k+l) cases where they differ.
    assert u_threshold(3, 2) == 3
    assert u_threshold(4, 1) == 3
    assert u_threshold(5, 2) == 4
    assert u_threshold(17, 2) == 10
    for k in range(2, 30):
        for l in range(1, 6):
            assert u_threshold(k, l) == -((k + l) // -2)
            assert 2 * u_threshold(k, l) >= k + l
            assert 2 * (u_threshold(k, l) - 1) < k + l


def test_bounds_match_the_evaluator_by_brute_force():
    # The window [lo, hi] is claimed to be exactly the plus-counts an avoiding AP
    # can have. Check it against the definition for every reachable plus-count.
    for k in range(2, 13):
        for l in range(1, 5):
            lo, hi = avoid_bounds(k, l)
            for p in range(0, k + 1):
                f = [1] * p + [-1] * (k - p)
                # {1..k} contains exactly one k-AP, namely 1..k with d = 1.
                assert avoids(f, k, l) == (lo <= p <= hi), (k, l, p)


def test_shared_b_for_both_directions():
    for k in range(2, 20):
        for l in range(1, 5):
            lo, hi = avoid_bounds(k, l)
            u = u_threshold(k, l)
            assert hi == u - 1
            assert lo == k - hi  # the negated-literal constraint is at-most-hi too


def test_l_one_odd_k_has_an_empty_window():
    for k in (3, 5, 7, 17):
        lo, hi = avoid_bounds(k, 1)
        assert lo > hi
    for k in (2, 4, 6, 16):
        lo, hi = avoid_bounds(k, 1)
        assert lo == hi == k // 2


def test_l_above_k_makes_the_constraint_vacuous():
    # |sum| <= k always, so l > k cannot be reached and every AP avoids. At
    # l = k the window is [1, k-1]: still a real constraint, just a weak one.
    for k in (2, 3, 5):
        assert avoid_bounds(k, k) == (1, k - 1)
        lo, hi = avoid_bounds(k, k + 1)
        assert lo <= 0 and hi >= k


def test_k17_l2_window():
    assert avoid_bounds(17, 2) == (8, 9)


def test_rejects_bad_arguments():
    with pytest.raises(ValueError):
        u_threshold(1, 2)
    with pytest.raises(ValueError):
        u_threshold(3, 0)
    with pytest.raises(TypeError):
        u_threshold(3.0, 2)
    with pytest.raises(TypeError):
        avoid_bounds(3, 2.0)
