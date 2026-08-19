"""Arithmetic progressions inside {1..N}, in the one order everything else assumes.

A ``k``-AP is ``a, a+d, ..., a+(k-1)d`` with ``d >= 1`` and every term in
``{1..N}``. The canonical order - ``d`` ascending, then ``a`` ascending - is
contract, not convenience: it fixes the AP index ``t`` used by the encoders'
auxiliary-variable blocks and therefore the byte layout of every CNF this repo
hashes. Changing it changes every golden sha256 in the suite.
"""

from __future__ import annotations

from collections.abc import Iterator


def _check(N: int, k: int) -> None:
    if isinstance(N, bool) or not isinstance(N, int):
        raise TypeError(f"N must be an int, got {type(N).__name__}")
    if isinstance(k, bool) or not isinstance(k, int):
        raise TypeError(f"k must be an int, got {type(k).__name__}")
    if k < 2:
        raise ValueError(f"k must be at least 2, got {k}")


def num_aps(N: int, k: int) -> int:
    """Number of ``k``-APs in ``{1..N}``, in closed form.

    For each ``d`` there are ``N - (k-1)*d`` starting points, and ``d`` runs to
    ``D = (N-1)//(k-1)``, so the total is ``D*N - (k-1)*D*(D+1)//2``.
    """
    _check(N, k)
    if N < k:
        return 0
    D = (N - 1) // (k - 1)
    return D * N - (k - 1) * D * (D + 1) // 2


def iter_aps(N: int, k: int) -> Iterator[tuple[int, ...]]:
    """Yield every ``k``-AP in ``{1..N}`` in canonical order, as a tuple of terms."""
    _check(N, k)
    if N < k:
        return
    for d in range(1, (N - 1) // (k - 1) + 1):
        for a in range(1, N - (k - 1) * d + 1):
            yield tuple(a + i * d for i in range(k))
