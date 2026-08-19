"""The one place the cardinality threshold is derived. Encoders read it; the
evaluator never does.

With ``p = #{n in P : f(n) = +1}`` and ``|P| = k`` we have ``sum_P f = 2p - k``,
so ``|sum_P f| < l`` iff ``(k-l)/2 < p < (k+l)/2``. Over the integers that is

    k - u + 1 <= p <= u - 1,   u = ceil((k+l)/2),

which is two at-most-``b`` constraints sharing ``b = u - 1``: one over the
positive literals of the AP (``p <= b``) and one over their negations
(``k - p <= b``, i.e. ``p >= k - b``).

``lo > hi`` means no ``p`` works, so avoidance is impossible for any ``N >= k``
and the encoders emit the empty clause. That happens exactly for ``l = 1`` with
odd ``k``, where every AP sum is odd and therefore never zero: ``N(k,1) = k``.
"""

from __future__ import annotations


def _check(k: int, l: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int):
        raise TypeError(f"k must be an int, got {type(k).__name__}")
    if isinstance(l, bool) or not isinstance(l, int):
        raise TypeError(f"l must be an int, got {type(l).__name__}")
    if k < 2:
        raise ValueError(f"k must be at least 2, got {k}")
    if l < 1:
        raise ValueError(f"l must be at least 1, got {l}")


def u_threshold(k: int, l: int) -> int:
    """``ceil((k+l)/2)``, computed in integers.

    ``-((k+l)//-2)`` is exact ceiling division; ``math.ceil((k+l)/2)`` would go
    through a float and is not used anywhere in this repo.
    """
    _check(k, l)
    return -((k + l) // -2)


def avoid_bounds(k: int, l: int) -> tuple[int, int]:
    """``(lo, hi)``: an AP avoids ``(k,l)`` iff its plus-count is in ``[lo, hi]``."""
    u = u_threshold(k, l)
    return k - u + 1, u - 1
