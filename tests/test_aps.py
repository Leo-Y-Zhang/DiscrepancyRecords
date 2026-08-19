"""Canonical AP order is contract, not convenience: it fixes the CNF byte layout."""

import pytest

from nk2.aps import iter_aps, num_aps


def test_closed_form_matches_enumeration():
    for k in range(2, 9):
        for n in range(0, 41):
            assert num_aps(n, k) == len(list(iter_aps(n, k))), (n, k)


def test_order_is_d_then_a_and_strictly_increasing():
    for k in range(2, 9):
        for n in range(0, 41):
            seen = []
            for ap in iter_aps(n, k):
                assert len(ap) == k
                d = ap[1] - ap[0]
                assert d >= 1
                assert list(ap) == [ap[0] + i * d for i in range(k)]
                assert 1 <= ap[0] and ap[-1] <= n
                seen.append((d, ap[0]))
            assert seen == sorted(seen), (n, k)
            assert len(set(seen)) == len(seen)


def test_first_and_last_ap_are_the_extremes():
    # d = 1, a = 1 comes first; the last AP has the largest d, which at N = 9,
    # k = 3 is d = 4 and admits only a = 1. M3 (dropping the last a of each d)
    # removes the maximal-a members below.
    aps = list(iter_aps(9, 3))
    assert aps[0] == (1, 2, 3)
    assert aps[-1] == (1, 5, 9)
    assert (7, 8, 9) in aps  # maximal a at d = 1
    assert (5, 7, 9) in aps  # maximal a at d = 2
    assert (3, 6, 9) in aps  # maximal a at d = 3


def test_no_aps_when_n_below_k():
    for k in range(2, 9):
        for n in range(0, k):
            assert num_aps(n, k) == 0
            assert list(iter_aps(n, k)) == []


def test_k_below_two_raises():
    for k in (1, 0, -3):
        with pytest.raises(ValueError):
            num_aps(10, k)
        with pytest.raises(ValueError):
            list(iter_aps(10, k))


def test_non_integer_arguments_raise():
    with pytest.raises(TypeError):
        num_aps(10.0, 3)
    with pytest.raises(TypeError):
        num_aps(10, 3.0)
    with pytest.raises(TypeError):
        list(iter_aps(10.0, 3))


def test_known_counts():
    assert num_aps(9, 3) == 16
    assert num_aps(272, 17) == 2176
    assert num_aps(273, 17) == 2193  # the campaign instance size, per the TDD
