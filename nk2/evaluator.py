"""Exact, solver-free decision of avoidance, straight from the definition.

``f`` avoids ``(k,l)`` iff every ``k``-AP ``P`` in ``{1..N}`` has
``|sum_{n in P} f(n)| < l``.

**Import boundary (load-bearing).** This module imports neither ``nk2.spec`` nor
any encoder, and never will. The encoders derive a threshold ``u`` and turn it
into cardinality constraints; if that derivation were wrong, an encoder checked
against another encoder would agree with itself and hide the error. Everything
here is integer arithmetic over ``+1`` and ``-1`` that has never seen ``u``, so
the equivalence tests compare two genuinely independent things.
``tests/test_import_boundary.py`` asserts this from the module source.

No floating point appears anywhere in this file, for the same reason.
"""

from __future__ import annotations

from nk2.aps import iter_aps


def _check_coloring(f: object) -> list[int]:
    if isinstance(f, (str, bytes)) or not isinstance(f, (list, tuple)):
        raise TypeError(f"coloring must be a list or tuple, got {type(f).__name__}")
    for i, v in enumerate(f):
        # bool is a subclass of int and True == 1, so it has to be excluded by
        # type rather than by value.
        if isinstance(v, bool) or not isinstance(v, int):
            raise TypeError(f"coloring[{i}] must be int +1 or -1, got {type(v).__name__}")
        if v not in (1, -1):
            raise ValueError(f"coloring[{i}] must be +1 or -1, got {v}")
    return list(f)


def _check_l(l: int) -> None:
    if isinstance(l, bool) or not isinstance(l, int):
        raise TypeError(f"l must be an int, got {type(l).__name__}")
    if l < 1:
        raise ValueError(f"l must be at least 1, got {l}")


def max_abs_ap_sum(f: list[int], k: int) -> tuple[int, tuple[int, ...] | None]:
    """Largest ``|sum_P f|`` over all ``k``-APs, with the first AP attaining it.

    Returns ``(0, None)`` when ``{1..len(f)}`` contains no ``k``-AP at all.
    """
    fv = _check_coloring(f)
    best = 0
    best_ap: tuple[int, ...] | None = None
    for ap in iter_aps(len(fv), k):
        s = 0
        for n in ap:
            s += fv[n - 1]
        a = -s if s < 0 else s
        if best_ap is None or a > best:
            best = a
            best_ap = ap
    return best, best_ap


def first_bad_ap(f: list[int], k: int, l: int) -> tuple[int, ...] | None:
    """First AP in canonical order with ``|sum_P f| >= l``, or ``None``."""
    fv = _check_coloring(f)
    _check_l(l)
    for ap in iter_aps(len(fv), k):
        s = 0
        for n in ap:
            s += fv[n - 1]
        if (-s if s < 0 else s) >= l:
            return ap
    return None


def avoids(f: list[int], k: int, l: int) -> bool:
    """True iff every ``k``-AP in ``{1..len(f)}`` has ``|sum_P f| < l``.

    Vacuously true when there is no ``k``-AP, which is the ``N < k`` case.
    """
    return first_bad_ap(f, k, l) is None
