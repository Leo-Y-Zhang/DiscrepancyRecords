"""One cardinality constraint at a time, exhaustively.

Both counter-based encoders must satisfy two separate properties, and it is the
second that dies quietly when a clause is dropped:

1. correctness - the constraint is satisfiable iff at most b inputs are true;
2. arc consistency - unit propagation alone refutes every violating input.
"""

import pytest
from pysat.solvers import Solver

from nk2 import encode_seqcount, encode_totalizer
from tests._minisolve import extends, up_conflict

ENCODERS = [encode_seqcount, encode_totalizer]
IDS = [m.NAME for m in ENCODERS]


def constraint(module, n, b):
    """Clauses for at-most-b over variables 1..n, and the total variable count."""
    clauses = list(module.at_most(list(range(1, n + 1)), b, n))
    return clauses, n + module.block_size(n, b)


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_every_assignment_extends_exactly_when_popcount_fits(module):
    for n in range(1, 11):
        for b in range(0, n + 1):
            clauses, _ = constraint(module, n, b)
            with Solver(name="minisat22", bootstrap_with=clauses) as solver:
                for mask in range(1 << n):
                    fixed = [(i + 1) if (mask >> i) & 1 else -(i + 1) for i in range(n)]
                    want = bin(mask).count("1") <= b
                    assert solver.solve(assumptions=fixed) is want, (module.NAME, n, b, mask)


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_unit_propagation_alone_refutes_every_violation(module):
    # M5 (seqcount bound b -> b+1) and M6 (dropped totalizer root unit) both
    # show up here first.
    for n in range(1, 11):
        for b in range(0, n + 1):
            clauses, _ = constraint(module, n, b)
            for mask in range(1 << n):
                fixed = {i + 1: bool((mask >> i) & 1) for i in range(n)}
                want = bin(mask).count("1") > b
                assert up_conflict(clauses, fixed) is want, (module.NAME, n, b, mask)


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_negated_inputs_work_the_same(module):
    # The c = 1 constraint of every AP is over negated literals.
    for n in range(1, 7):
        for b in range(0, n + 1):
            lits = [-(i + 1) for i in range(n)]
            clauses = list(module.at_most(lits, b, n))
            n_vars = n + module.block_size(n, b)
            for mask in range(1 << n):
                fixed = {i + 1: bool((mask >> i) & 1) for i in range(n)}
                want = (n - bin(mask).count("1")) <= b
                assert extends(n_vars, clauses, fixed) is want, (module.NAME, n, b, mask)


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_block_size_matches_the_variables_actually_used(module):
    for n in range(1, 13):
        for b in range(0, n + 2):
            clauses = list(module.at_most(list(range(1, n + 1)), b, n))
            used = {abs(lit) for clause in clauses for lit in clause}
            aux = {v for v in used if v > n}
            assert len(aux) == module.block_size(n, b), (module.NAME, n, b)
            if aux:
                assert min(aux) == n + 1
                assert max(aux) == n + module.block_size(n, b)


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_boundary_bounds(module):
    n = 6
    # b = 0: units, no auxiliary variables.
    clauses = list(module.at_most([1, 2, 3, 4, 5, 6], 0, n))
    assert clauses == [[-1], [-2], [-3], [-4], [-5], [-6]]
    assert module.block_size(n, 0) == 0
    # b = k-1: forbids the all-true assignment only.
    clauses, _ = constraint(module, n, n - 1)
    with Solver(name="minisat22", bootstrap_with=clauses) as solver:
        assert not solver.solve(assumptions=[1, 2, 3, 4, 5, 6])
        assert solver.solve(assumptions=[1, 2, 3, 4, 5, -6])
    # b >= k: vacuous, nothing emitted.
    assert list(module.at_most([1, 2, 3], 3, 3)) == []
    assert list(module.at_most([1, 2, 3], 9, 3)) == []
    assert module.block_size(3, 3) == 0


def test_totalizer_uses_a_balanced_split_not_a_chain():
    # M7: splitting m//2 -> 1 turns the tree into a chain, which changes the
    # auxiliary count. Balanced T(8,3) = T(4)+T(4)+4 = 8+8+4 = 20; the chain
    # T(8,3) = T(1)+T(7)+4 unrolls to 25.
    assert encode_totalizer.block_size(8, 3) == 20
    assert encode_totalizer.block_size(17, 9) == 63

    def chain_size(m, b):
        return 0 if m <= 1 else chain_size(1, b) + chain_size(m - 1, b) + min(m, b + 1)

    assert chain_size(8, 3) == 25
    assert encode_totalizer.block_size(8, 3) != chain_size(8, 3)


def test_the_two_encoders_do_not_agree_on_size():
    # If they did, that would be a sign they are not structurally independent.
    for n, b in ((8, 3), (17, 9), (5, 2)):
        assert encode_seqcount.block_size(n, b) != encode_totalizer.block_size(n, b)
