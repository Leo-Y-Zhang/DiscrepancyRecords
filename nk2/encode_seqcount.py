"""Encoder 2 of 3: Sinz sequential counter.

A register ``s_{i,j}``, ``i = 1..k-1``, ``j = 1..b``, carries "at least ``j`` of
the first ``i`` literals are true" up the chain, and the final group of clauses
forbids the carry reaching ``b+1``. ``B_seq = (k-1)*b`` auxiliary variables per
constraint, laid out at ``base + (i-1)*b + j``.

Auxiliary blocks are addressed by ``base(t,c) = N + (2*t + c) * B``, with ``t``
the AP index in canonical order and ``c = 0`` for the positive-literal
constraint, ``c = 1`` for the negated one. The formula is closed, so any
constraint's numbering is computable without generating the ones before it.

This module hand-rolls its own counter and shares no helper with
``encode_subsets`` or ``encode_totalizer``; see the note in ``encode_subsets``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from nk2.aps import iter_aps, num_aps
from nk2.spec import avoid_bounds

NAME = "seqcount"


def block_size(k: int, b: int) -> int:
    """Auxiliary variables used by one at-most-``b`` constraint over ``k`` literals."""
    if b <= 0 or b >= k:
        return 0
    return (k - 1) * b


def num_vars(N: int, k: int, l: int) -> int:
    lo, hi = avoid_bounds(k, l)
    if lo > hi:
        return max(N, 0)
    return max(N, 0) + 2 * num_aps(N, k) * block_size(k, hi)


def at_most(lits: Sequence[int], b: int, base: int) -> Iterator[list[int]]:
    """At most ``b`` of ``lits`` true, using ``base+1 .. base+block_size`` as aux."""
    k = len(lits)
    if b >= k:
        return  # vacuous
    if b == 0:
        for lit in lits:
            yield [-lit]
        return
    if b < 0:
        yield []
        return

    def s(i: int, j: int) -> int:
        return base + (i - 1) * b + j

    yield [-lits[0], s(1, 1)]
    for j in range(2, b + 1):
        yield [-s(1, j)]
    for i in range(2, k):
        yield [-lits[i - 1], s(i, 1)]
        yield [-s(i - 1, 1), s(i, 1)]
        for j in range(2, b + 1):
            yield [-lits[i - 1], -s(i - 1, j - 1), s(i, j)]
            yield [-s(i - 1, j), s(i, j)]
    for i in range(2, k + 1):
        yield [-lits[i - 1], -s(i - 1, b)]


def build(
    N: int, k: int, l: int, symmetry_break: bool = False
) -> tuple[int, Iterator[list[int]]]:
    return num_vars(N, k, l), _clauses(N, k, l, symmetry_break)


def _clauses(N: int, k: int, l: int, symmetry_break: bool) -> Iterator[list[int]]:
    lo, hi = avoid_bounds(k, l)
    b = hi
    block = block_size(k, b)
    degenerate = lo > hi
    for t, ap in enumerate(iter_aps(N, k)):
        if degenerate:
            yield []
            continue
        yield from at_most(ap, b, N + (2 * t) * block)
        yield from at_most([-n for n in ap], b, N + (2 * t + 1) * block)
    if symmetry_break and N >= 1:
        # Sound because avoidance is invariant under f -> -f. Off by default.
        yield [1]
