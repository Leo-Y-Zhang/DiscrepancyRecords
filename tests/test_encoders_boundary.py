"""Degenerate cases, closed-form variable counts, and the golden hash pins.

The hashes below are a regression pin on the exact bytes of an instance. They
move if the AP order changes, if a variable block moves, if the totalizer splits
differently, or if the writer ever emits CRLF - which is the whole point.
"""

import hashlib

import pytest
from pysat.solvers import Solver

from nk2 import encode_seqcount, encode_subsets, encode_totalizer
from nk2.aps import num_aps
from nk2.dimacs import write_cnf
from nk2.encode_subsets import InstanceTooLarge
from nk2.evaluator import avoids
from nk2.spec import avoid_bounds

ENCODERS = [encode_subsets, encode_seqcount, encode_totalizer]
IDS = [m.NAME for m in ENCODERS]

GOLDEN = {
    ("subsets", 9, 3, 2, False): (
        "5f598f1988ddae897767666dff22b44f235ed872a98b93713a612ac01b1690ab",
        9,
        32,
    ),
    ("subsets", 9, 3, 2, True): (
        "e782ee8aa758549ee77f189b8d0654a89b6b73925b522a10aeecfff37097a9a0",
        9,
        33,
    ),
    ("subsets", 13, 5, 2, False): (
        "ac379e9c72cf342a9d55d30cfc50a5df7ee68afce92846ec0c9a497998df1284",
        13,
        150,
    ),
    ("subsets", 13, 5, 2, True): (
        "4f92ff6c7d54b5923b624675f1901425165fd6106d4c1b72ff48df401219afe6",
        13,
        151,
    ),
    ("seqcount", 9, 3, 2, False): (
        "9290f8f69df949e1f52589eb7680fe0a7958d6963b0db5bcb4c640eb55105ce9",
        137,
        256,
    ),
    ("seqcount", 9, 3, 2, True): (
        "d0d1bdad8413380ebe7470a082d78eb13838cb1a4500a4907fc4a9db4dcb7a30",
        137,
        257,
    ),
    ("seqcount", 13, 5, 2, False): (
        "1ed19c06add41bdfac69186400aec091f2e745f0ea1518abaedcab71f3825b65",
        373,
        750,
    ),
    ("seqcount", 13, 5, 2, True): (
        "2826534e9f9061367a81786515e497cce888b55352777681e3a6ffa6d4c6abd0",
        373,
        751,
    ),
    ("totalizer", 9, 3, 2, False): (
        "26512e11dd590b4bc023885c9a1440ddee643a4e18f32a05d597c1a5178a2f81",
        169,
        288,
    ),
    ("totalizer", 9, 3, 2, True): (
        "a633a25e279130a3e507a0e52787fd7281bc8b99cdd2df882fea1219d3e853af",
        169,
        289,
    ),
    ("totalizer", 13, 5, 2, False): (
        "46b182c9c6f36fa0695233c9fe13967080685ba714beba7a080fb4241e5f684a",
        343,
        660,
    ),
    ("totalizer", 13, 5, 2, True): (
        "5e1b5cd7dc6f9e54418da8e3162652ef2006414042af337227b16a2a5494eed2",
        343,
        661,
    ),
}


def write(module, tmp_path, N, k, l, sb=False):
    n_vars, clauses = module.build(N, k, l, symmetry_break=sb)
    return write_cnf(tmp_path / "i.cnf", n_vars, clauses)


@pytest.mark.parametrize("key", sorted(GOLDEN), ids=lambda key: "_".join(str(p) for p in key))
def test_golden_instance_hashes(key, tmp_path):
    name, N, k, l, sb = key
    module = next(m for m in ENCODERS if m.NAME == name)
    want_sha, want_vars, want_clauses = GOLDEN[key]
    info = write(module, tmp_path, N, k, l, sb)
    assert (info["sha256"], info["n_vars"], info["n_clauses"]) == (
        want_sha,
        want_vars,
        want_clauses,
    )
    # And the hash really is over the file as it sits on disk.
    assert hashlib.sha256((tmp_path / "i.cnf").read_bytes()).hexdigest() == want_sha


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_no_aps_gives_an_empty_instance(module, tmp_path):
    # N < k: nothing to constrain, so every coloring avoids vacuously.
    for N, k in ((4, 5), (1, 3), (0, 3), (16, 17)):
        info = write(module, tmp_path, N, k, 2)
        assert info["n_clauses"] == 0
        assert (tmp_path / "i.cnf").read_bytes() == f"p cnf {N} 0\n".encode("ascii")
        assert avoids([1] * N, k, 2)


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_l_one_odd_k_emits_the_empty_clause(module, tmp_path):
    # lo > hi: no plus-count avoids, so N(k,1) = k. One empty clause per AP.
    for k in (3, 5, 7):
        lo, hi = avoid_bounds(k, 1)
        assert lo > hi
        info = write(module, tmp_path, k, k, 1)
        assert info["n_clauses"] == num_aps(k, k) == 1
        assert (tmp_path / "i.cnf").read_bytes() == f"p cnf {k} 1\n0\n".encode("ascii")
        with Solver(name="minisat22", bootstrap_with=list(module.build(k, k, 1)[1])) as s:
            assert not s.solve()
        # One position short, there is no AP at all and it is satisfiable.
        with Solver(name="minisat22", bootstrap_with=list(module.build(k - 1, k, 1)[1])) as s:
            assert s.solve()


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_k_equals_two(module):
    # N(2,2) = 3: |f(a) + f(a+d)| < 2 forces every pair to differ, impossible at N = 3.
    with Solver(name="minisat22", bootstrap_with=list(module.build(2, 2, 2)[1])) as s:
        assert s.solve()
    with Solver(name="minisat22", bootstrap_with=list(module.build(3, 2, 2)[1])) as s:
        assert not s.solve()


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_num_vars_is_a_closed_form_matching_the_body(module, tmp_path):
    for N in range(0, 15):
        for k, l in ((3, 2), (5, 2), (4, 1), (5, 1), (2, 2)):
            n_vars, clauses = module.build(N, k, l)
            clauses = list(clauses)
            assert n_vars == module.num_vars(N, k, l)
            used = {abs(lit) for clause in clauses for lit in clause}
            assert not used or max(used) <= n_vars, (module.NAME, N, k, l)


@pytest.mark.parametrize("module", ENCODERS, ids=IDS)
def test_written_instance_is_valid_dimacs(module, tmp_path):
    # M9: a header that under-counts variables would produce a literal out of
    # range. Parse the file back rather than trusting the writer's return value.
    info = write(module, tmp_path, 13, 5, 2)
    lines = (tmp_path / "i.cnf").read_text(encoding="ascii").splitlines()
    head = lines[0].split()
    assert head[:2] == ["p", "cnf"]
    n_vars, n_clauses = int(head[2]), int(head[3])
    assert (n_vars, n_clauses) == (info["n_vars"], info["n_clauses"])
    assert len(lines) - 1 == n_clauses
    for line in lines[1:]:
        lits = [int(tok) for tok in line.split()]
        assert lits[-1] == 0
        assert all(1 <= abs(lit) <= n_vars for lit in lits[:-1])


def test_variable_one_is_position_one_in_every_encoder():
    # A model decodes to a coloring with no lookup table only because var(x_n) = n
    # in all three encoders.
    for module in ENCODERS:
        clauses = list(module.build(9, 3, 2)[1])
        main = {abs(lit) for clause in clauses for lit in clause if abs(lit) <= 9}
        assert main == set(range(1, 10)), module.NAME


def test_subsets_refuses_the_campaign_scale_instance():
    # 2 * 2193 * C(17,10) = about 85 million clauses. The guard is the point:
    # encoding diversity is bought by having one encoder that cannot scale.
    with pytest.raises(InstanceTooLarge):
        encode_subsets.build(273, 17, 2)
    # It still reports the size without building anything.
    assert encode_subsets.num_clauses_estimate(273, 17, 2) > 5_000_000


def test_counter_encoders_handle_the_campaign_scale_header():
    # Closed-form var counts at k = 17, N = 273, as quoted in the TDD.
    assert num_aps(273, 17) == 2193
    assert encode_seqcount.block_size(17, 9) == 144
    assert encode_seqcount.num_vars(273, 17, 2) == 273 + 2 * 2193 * 144
    assert encode_totalizer.num_vars(273, 17, 2) < encode_seqcount.num_vars(273, 17, 2)
