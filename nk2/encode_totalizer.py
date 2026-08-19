"""Encoder 3 of 3: truncated Bailleux-Boufkhad totalizer.

A balanced binary tree over the ``k`` literals in AP order; a node over ``m > 1``
leaves splits into ``left = m//2`` and ``right = m - m//2``, and carries
``r = min(m, b+1)`` outputs, ``O_j`` meaning "at least ``j`` of this node's
leaves are true". Truncating at ``b+1`` is what keeps the encoding small: once
``b+1`` literals are true the constraint is already violated, so counting higher
is wasted.

Variable blocks are allocated **post-order** - left subtree, then right subtree,
then the node itself - with ``var(O_j) = node_base + j - 1``. The per-constraint
size is ``T(k)`` where ``T(m) = 0`` for ``m <= 1`` and otherwise
``T(m//2) + T(m - m//2) + min(m, b+1)``.

The auxiliary count, the implication graph and the clause widths all differ from
the sequential counter's: the diversity between encoders is structural, not
cosmetic. This module shares no helper with the other two; see the note in
``encode_subsets``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from nk2.aps import iter_aps, num_aps
from nk2.spec import avoid_bounds

NAME = "totalizer"


def _tree_size(m: int, b: int) -> int:
    if m <= 1:
        return 0
    return _tree_size(m // 2, b) + _tree_size(m - m // 2, b) + min(m, b + 1)


def block_size(k: int, b: int) -> int:
    """Auxiliary variables used by one at-most-``b`` constraint over ``k`` literals."""
    if b <= 0 or b >= k:
        return 0
    return _tree_size(k, b)


def num_vars(N: int, k: int, l: int) -> int:
    lo, hi = avoid_bounds(k, l)
    if lo > hi:
        return max(N, 0)
    return max(N, 0) + 2 * num_aps(N, k) * block_size(k, hi)


def _node(
    lits: Sequence[int], b: int, cursor: list[int]
) -> tuple[list[int], list[list[int]]]:
    """Return this node's output literals and the clauses of its whole subtree."""
    m = len(lits)
    if m == 1:
        return [lits[0]], []  # a leaf's single output is the literal itself
    split = m // 2
    left, left_clauses = _node(lits[:split], b, cursor)
    right, right_clauses = _node(lits[split:], b, cursor)

    r = min(m, b + 1)
    node_base = cursor[0]
    cursor[0] += r
    out = [node_base + j - 1 for j in range(1, r + 1)]

    clauses = left_clauses + right_clauses
    for alpha in range(len(left) + 1):
        for beta in range(len(right) + 1):
            total = alpha + beta
            if not 1 <= total <= r:
                continue
            clause = []
            if alpha:
                clause.append(-left[alpha - 1])
            if beta:
                clause.append(-right[beta - 1])
            clause.append(out[total - 1])
            clauses.append(clause)
    return out, clauses


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

    cursor = [base + 1]
    out, clauses = _node(list(lits), b, cursor)
    yield from clauses
    # b < k here, so the root carries r = b+1 outputs and O_{b+1} exists.
    yield [-out[b]]


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
