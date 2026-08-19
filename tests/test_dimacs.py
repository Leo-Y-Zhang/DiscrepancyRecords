"""The DIMACS writer's output is hashed, so its bytes are part of the contract."""

import hashlib

import pytest

from nk2.dimacs import DimacsError, write_cnf


def test_header_body_and_exact_bytes(tmp_path):
    p = tmp_path / "a.cnf"
    info = write_cnf(p, 3, [[1, -2], [3]])
    assert p.read_bytes() == b"p cnf 3 2\n1 -2 0\n3 0\n"
    assert info["n_vars"] == 3
    assert info["n_clauses"] == 2
    assert info["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()


def test_no_carriage_returns(tmp_path):
    # M8: a writer emitting CRLF would change every golden hash. Assert the bytes
    # directly as well, so the reason a hash moved is legible.
    p = tmp_path / "a.cnf"
    write_cnf(p, 2, [[1, 2], [-1, -2]])
    assert b"\r" not in p.read_bytes()


def test_zero_clauses(tmp_path):
    p = tmp_path / "empty.cnf"
    info = write_cnf(p, 5, [])
    assert p.read_bytes() == b"p cnf 5 0\n"
    assert info["n_clauses"] == 0


def test_empty_clause_is_written_as_a_bare_zero(tmp_path):
    p = tmp_path / "unsat.cnf"
    info = write_cnf(p, 4, [[]])
    assert p.read_bytes() == b"p cnf 4 1\n0\n"
    assert info["n_clauses"] == 1


def test_literals_keep_emission_order_and_are_not_sorted(tmp_path):
    p = tmp_path / "a.cnf"
    write_cnf(p, 5, [[5, -1, 3]])
    assert p.read_bytes() == b"p cnf 5 1\n5 -1 3 0\n"


def test_exactly_one_trailing_newline(tmp_path):
    p = tmp_path / "a.cnf"
    write_cnf(p, 2, [[1], [2]])
    raw = p.read_bytes()
    assert raw.endswith(b"2 0\n")
    assert not raw.endswith(b"\n\n")


def test_streams_a_generator_without_materialising_it(tmp_path):
    p = tmp_path / "a.cnf"
    info = write_cnf(p, 3, ([i] for i in (1, 2, 3)))
    assert info["n_clauses"] == 3
    assert p.read_bytes() == b"p cnf 3 3\n1 0\n2 0\n3 0\n"


def test_literal_outside_the_declared_range_is_refused(tmp_path):
    # M9: a header that ignores auxiliary variables produces an invalid instance.
    # Refuse at write time rather than emit something a solver may reject.
    p = tmp_path / "a.cnf"
    with pytest.raises(DimacsError):
        write_cnf(p, 2, [[1, 3]])
    with pytest.raises(DimacsError):
        write_cnf(p, 2, [[-3]])


def test_zero_literal_is_refused(tmp_path):
    with pytest.raises(DimacsError):
        write_cnf(tmp_path / "a.cnf", 2, [[1, 0]])


def test_temp_file_is_cleaned_up(tmp_path):
    p = tmp_path / "a.cnf"
    write_cnf(p, 2, [[1]])
    assert sorted(q.name for q in tmp_path.iterdir()) == ["a.cnf"]
    with pytest.raises(DimacsError):
        write_cnf(tmp_path / "b.cnf", 2, [[9]])
    assert not any(q.name.endswith(".tmp") for q in tmp_path.iterdir())


def test_creates_missing_parent_directories(tmp_path):
    p = tmp_path / "deep" / "deeper" / "a.cnf"
    write_cnf(p, 1, [[1]])
    assert p.exists()
