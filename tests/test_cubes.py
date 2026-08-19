"""The cube construction is what makes a wave's completeness checkable, so it is
pinned from both ends: the literal-level definition, and the byte-level rule
that a cube instance is the base with units appended and the header bumped.
"""

import hashlib
from pathlib import Path

import pytest

from nk2 import encode_seqcount
from nk2.cubes import (
    CONSTRUCTION,
    CubeError,
    cube_clauses,
    cube_literals,
    cubes_sha256,
    cubes_text,
    n_cubes,
    write_cube_cnf,
)
from nk2.dimacs import write_cnf


def test_construction_is_the_documented_name():
    # A manifest names the construction it used. If this string ever changes,
    # every manifest recorded under the old one has to be refused, not guessed
    # at, so the change has to be deliberate.
    assert CONSTRUCTION == "mask-lsb-first.v1"


def test_literals_are_least_significant_bit_first():
    split = [2, 5]
    assert cube_literals(split, 0) == [-2, -5]
    assert cube_literals(split, 1) == [2, -5]
    assert cube_literals(split, 2) == [-2, 5]
    assert cube_literals(split, 3) == [2, 5]


def test_the_cube_set_is_every_assignment_exactly_once():
    split = [3, 7, 11, 12]
    assert n_cubes(split) == 16
    seen = {tuple(cube_literals(split, i)) for i in range(16)}
    assert len(seen) == 16
    # Every sign pattern over the split variables appears: that is the property
    # the whole wave argument rests on.
    for pattern in range(16):
        want = tuple(v if (pattern >> j) & 1 else -v for j, v in enumerate(split))
        assert want in seen


def test_cube_file_bytes_are_the_documented_format():
    assert cubes_text([2, 5]) == "a -2 -5 0\na 2 -5 0\na -2 5 0\na 2 5 0\n"
    assert cubes_sha256([2, 5]) == hashlib.sha256(cubes_text([2, 5]).encode("ascii")).hexdigest()


def test_cube_file_is_ascii_and_lf_only():
    raw = cubes_text([1, 4, 9]).encode("ascii")
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert len(raw.splitlines()) == 8


@pytest.mark.parametrize(
    "split",
    [[], [0], [-3], [2, 2], [True], ["4"], list(range(1, 30))],
    ids=["empty", "zero", "negative", "repeat", "bool", "string", "too-many"],
)
def test_a_split_that_is_not_a_partition_is_refused(split):
    with pytest.raises(CubeError):
        n_cubes(split)


def test_split_above_the_main_variables_is_refused():
    from nk2.cubes import check_split

    check_split([9], n_main=9)
    with pytest.raises(CubeError):
        check_split([10], n_main=9)


def test_cube_index_out_of_range_is_refused():
    with pytest.raises(CubeError):
        cube_literals([2, 5], 4)
    with pytest.raises(CubeError):
        cube_literals([2, 5], -1)


def test_cube_instance_equals_the_base_clauses_plus_the_units(tmp_path: Path):
    # The "append units, raise the header count" rule, checked against the
    # independent writer that produced the base. If either drifts, the cube a
    # solver saw stops being the cube the gate re-derives.
    split = [2, 5]
    n_vars, clauses = encode_seqcount.build(9, 3, 2, symmetry_break=True)
    base = write_cnf(tmp_path / "base.cnf", n_vars, clauses)

    for index in range(4):
        n_vars2, clauses2 = encode_seqcount.build(9, 3, 2, symmetry_break=True)
        direct = write_cnf(
            tmp_path / f"direct{index}.cnf",
            n_vars2,
            list(clauses2) + cube_clauses(split, index),
        )
        appended = write_cube_cnf(base["path"], split, index, tmp_path / f"cube{index}.cnf")
        assert appended["sha256"] == direct["sha256"]
        assert appended["n_clauses"] == base["n_clauses"] + len(split)
        assert appended["n_vars"] == base["n_vars"]
        assert (tmp_path / f"cube{index}.cnf").read_bytes() == (
            tmp_path / f"direct{index}.cnf"
        ).read_bytes()


def test_cube_instance_refuses_a_base_without_a_header(tmp_path: Path):
    broken = tmp_path / "broken.cnf"
    broken.write_bytes(b"1 2 0\n")
    with pytest.raises(CubeError):
        write_cube_cnf(broken, [1], 0, tmp_path / "out.cnf")


def test_cube_instance_refuses_a_split_variable_the_base_does_not_declare(tmp_path: Path):
    base = write_cnf(tmp_path / "base.cnf", 4, [[1, 2], [-3, 4]])
    with pytest.raises(CubeError):
        write_cube_cnf(base["path"], [5], 0, tmp_path / "out.cnf")
