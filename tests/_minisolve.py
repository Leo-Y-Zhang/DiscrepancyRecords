"""A deliberately tiny UP + DPLL checker, so encoder tests do not lean entirely
on one third-party solver. Correctness of this file is cross-checked against
python-sat in tests/test_minisolve.py."""

from __future__ import annotations


def _propagate(clauses, assign) -> bool:
    """Unit-propagate in place. False on conflict."""
    changed = True
    while changed:
        changed = False
        for clause in clauses:
            unassigned = []
            satisfied = False
            for lit in clause:
                value = assign.get(abs(lit))
                if value is None:
                    unassigned.append(lit)
                elif value == (lit > 0):
                    satisfied = True
                    break
            if satisfied:
                continue
            if not unassigned:
                return False
            if len(unassigned) == 1:
                lit = unassigned[0]
                assign[abs(lit)] = lit > 0
                changed = True
    return True


def up_conflict(clauses, fixed) -> bool:
    """True iff unit propagation alone refutes ``fixed``.

    An arc-consistent cardinality encoding must refute every violating input
    assignment by propagation alone; dropping a clause breaks that long before
    it breaks satisfiability.
    """
    return not _propagate(list(clauses), dict(fixed))


def extends(n_vars: int, clauses, fixed) -> bool:
    """True iff the partial assignment ``fixed`` extends to a model."""
    clauses = [tuple(c) for c in clauses]

    def search(assign) -> bool:
        if not _propagate(clauses, assign):
            return False
        free = [v for v in range(1, n_vars + 1) if v not in assign]
        if not free:
            return True
        # Try all-false first: the auxiliary variables of both cardinality
        # encodings are monotone, so this settles the common case without
        # branching.
        guess = dict(assign)
        for v in free:
            guess[v] = False
        if _propagate(clauses, guess):
            return True
        for value in (True, False):
            branch = dict(assign)
            branch[free[0]] = value
            if search(branch):
                return True
        return False

    return search(dict(fixed))
