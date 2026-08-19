"""Three encoders against arithmetic that never saw the threshold u.

This is the cross-check the whole repo rests on. The evaluator computes AP sums
in integers and imports neither spec nor an encoder, so agreement here is
evidence about the encoders rather than three copies of one mistake.
"""

import pytest
from pysat.solvers import Solver

from nk2 import encode_seqcount, encode_subsets, encode_totalizer
from nk2.evaluator import avoids
from tests._minisolve import extends, up_conflict

ENCODERS = [encode_subsets, encode_seqcount, encode_totalizer]
IDS = [m.NAME for m in ENCODERS]
COUNTERS = [encode_seqcount, encode_totalizer]

# (4,1) has a one-point window; (5,1) has an empty one and must come out UNSAT
# for every N >= 5. (3,3) has a window wide enough to be vacuous at k = 3.
PARAMS = [(3, 2), (3, 3), (4, 2), (5, 2), (6, 2), (4, 1), (5, 1)]
MAX_N = 12
MAX_N_UP = 8  # unit propagation runs in pure Python; see the note in the report


def colorings(n):
    for mask in range(1 << n):
        yield mask, [1 if (mask >> i) & 1 else -1 for i in range(n)]


def avoid_masks(n, k, l):
    return {mask for mask, f in colorings(n) if avoids(f, k, l)}


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
@pytest.mark.parametrize(("k", "l"), PARAMS)
def test_encoder_models_are_exactly_the_avoiding_colorings(module, k, l):
    for n in range(1, MAX_N + 1):
        want = avoid_masks(n, k, l)
        n_vars, clause_iter = module.build(n, k, l)
        clauses = list(clause_iter)
        assert n_vars == module.num_vars(n, k, l)
        with Solver(name="minisat22", bootstrap_with=clauses) as solver:
            got = set()
            for mask in range(1 << n):
                fixed = [(i + 1) if (mask >> i) & 1 else -(i + 1) for i in range(n)]
                if solver.solve(assumptions=fixed):
                    got.add(mask)
        assert got == want, (module.NAME, k, l, n)


@pytest.mark.parametrize("module", COUNTERS, ids=[m.NAME for m in COUNTERS])
@pytest.mark.parametrize(("k", "l"), PARAMS)
def test_unit_propagation_decides_every_fixed_input(module, k, l):
    # Dropping a clause from either counter usually leaves the instance
    # satisfiable-equivalent but no longer arc-consistent, so this notices
    # first.
    for n in range(1, MAX_N_UP + 1):
        clauses = list(module.build(n, k, l)[1])
        for mask, f in colorings(n):
            fixed = {i + 1: bool((mask >> i) & 1) for i in range(n)}
            assert up_conflict(clauses, fixed) is (not avoids(f, k, l)), (
                module.NAME, k, l, n, mask
            )


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_minisolve_agrees_with_the_matrix(module):
    # Validates tests/_minisolve.py itself, so its verdicts above can be trusted.
    for k, l in ((3, 2), (4, 2), (5, 1)):
        for n in range(1, 8):
            n_vars, clause_iter = module.build(n, k, l)
            clauses = list(clause_iter)
            for mask, f in colorings(n):
                fixed = {i + 1: bool((mask >> i) & 1) for i in range(n)}
                assert extends(n_vars, clauses, fixed) is avoids(f, k, l), (
                    module.NAME, k, l, n, mask
                )


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_k3_anchor_is_satisfiable_at_8_and_unsatisfiable_at_9(module):
    # a(3) = 9: N(3,2) = 9. This is the smallest end-to-end anchor in the repo.
    n_vars, clause_iter = module.build(8, 3, 2)
    with Solver(name="minisat22", bootstrap_with=list(clause_iter)) as solver:
        assert solver.solve()
        model = solver.get_model()
        f = [1 if model[n - 1] > 0 else -1 for n in range(1, 9)]
        assert avoids(f, 3, 2)
    n_vars, clause_iter = module.build(9, 3, 2)
    with Solver(name="minisat22", bootstrap_with=list(clause_iter)) as solver:
        assert not solver.solve()


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_symmetry_break_preserves_satisfiability(module):
    for k, l in ((3, 2), (4, 2)):
        for n in range(1, 11):
            plain = list(module.build(n, k, l, symmetry_break=False)[1])
            broken = list(module.build(n, k, l, symmetry_break=True)[1])
            with Solver(name="minisat22", bootstrap_with=plain) as a:
                with Solver(name="minisat22", bootstrap_with=broken) as b:
                    assert a.solve() == b.solve(), (module.NAME, k, l, n)


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_symmetry_break_is_off_by_default(module):
    # M18: on by default would silently change every instance hash on record.
    clauses = list(module.build(9, 3, 2)[1])
    assert [1] not in clauses
    assert clauses.count([1]) == 0
    broken = list(module.build(9, 3, 2, symmetry_break=True)[1])
    assert broken == clauses + [[1]]
