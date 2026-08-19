"""Encoder 1 of 3: naive subset (pairwise-generalised) encoding.

At most ``b`` of ``k`` literals is stated directly - forbid every ``(b+1)``-subset
from being all-true. With ``b = u - 1`` that is every ``u``-subset, and the two
directions of the avoidance window become, per AP ``P`` and per ``u``-subset
``S`` of it, the pair of clauses ``[-s for s in S]`` and ``[+s for s in S]``.

No auxiliary variables at all, so ``num_vars = N`` and a model is a coloring with
no decoding. That makes this encoder the reference the other two are checked
against - and it is unusable at campaign scale, which is why the other two exist.
``InstanceTooLarge`` is raised rather than silently emitting tens of millions of
clauses.

This module hand-rolls its own cardinality reasoning and shares no helper with
``encode_seqcount`` or ``encode_totalizer``. A shared helper would give all
three encoders a common failure mode and destroy the only cross-check this
repo has.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from itertools import combinations
from math import comb

from nk2.aps import iter_aps, num_aps
from nk2.spec import avoid_bounds

NAME = "subsets"
MAX_CLAUSES = 5_000_000


class InstanceTooLarge(ValueError):
    """This encoding would emit more clauses than MAX_CLAUSES."""


def block_size(k: int, b: int) -> int:
    """Auxiliary variables per cardinality constraint: none, by construction."""
    return 0


def num_vars(N: int, k: int, l: int) -> int:
    """Closed form, needed before the body is generated because the header
    carries it."""
    avoid_bounds(k, l)  # argument validation
    return max(N, 0)


def num_clauses_estimate(N: int, k: int, l: int) -> int:
    lo, hi = avoid_bounds(k, l)
    aps = num_aps(N, k)
    if lo > hi:
        return aps
    u = hi + 1
    if u > k:
        return 0
    return 2 * aps * comb(k, u)


def build(
    N: int, k: int, l: int, symmetry_break: bool = False
) -> tuple[int, Iterator[list[int]]]:
    size = num_clauses_estimate(N, k, l)
    if size > MAX_CLAUSES:
        raise InstanceTooLarge(
            f"subsets encoding of (N={N}, k={k}, l={l}) needs {size} clauses, "
            f"over the {MAX_CLAUSES} limit; use seqcount or totalizer"
        )
    return num_vars(N, k, l), _clauses(N, k, l, symmetry_break)


def _clauses(N: int, k: int, l: int, symmetry_break: bool) -> Iterator[list[int]]:
    lo, hi = avoid_bounds(k, l)
    u = hi + 1
    degenerate = lo > hi
    for ap in iter_aps(N, k):
        if degenerate:
            # No plus-count avoids, so no coloring of {1..N} can: say so directly.
            yield []
            continue
        if u > k:
            # b >= k: at most b of k literals is vacuous, both ways round.
            continue
        for subset in combinations(ap, u):
            yield [-s for s in subset]
            yield [s for s in subset]
    if symmetry_break and N >= 1:
        yield from _symmetry_clause()


def _symmetry_clause() -> Iterator[list[int]]:
    """Fix ``f(1) = +1``.

    Sound because avoidance is invariant under ``f -> -f``: negating a coloring
    negates every AP sum and leaves every absolute value unchanged. Off by
    default, because it changes the instance hash and a claim should be about the
    plain instance unless it says otherwise.
    """
    yield [1]


def at_most(lits: Sequence[int], b: int, base: int) -> Iterator[list[int]]:
    """At most ``b`` of ``lits`` true, as subset clauses. ``base`` is unused."""
    k = len(lits)
    if b >= k:
        return
    if b < 0:
        yield []
        return
    for subset in combinations(lits, b + 1):
        yield [-s for s in subset]
