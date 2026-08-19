"""The cube decomposition a cube-and-conquer wave splits an instance along.

A wave takes one base instance and replaces it with ``2**s`` derived instances,
one per assignment of ``s`` chosen split variables. The union of those cases is
the base instance again, so the base is UNSAT iff every cube is - but only if
the cube set really is every assignment. That "only if" is the whole soundness
argument, and it is not something a gate can take on trust from a file somebody
wrote: this module derives the cube set from the split alone, so the gate can
re-derive it and hash the result rather than reading a cubes file and believing
it.

**Construction** (``CONSTRUCTION`` names this version, and a manifest that names
anything else is refused rather than guessed at). For split variables
``[v_0 .. v_{s-1}]`` and cube index ``i`` in ``0 .. 2**s - 1``, literal ``j`` is
``+v_j`` when bit ``j`` of ``i`` is set and ``-v_j`` when it is not - least
significant bit first, in the order the split variables are listed. The cube
file is one ``a`` line per cube in index order:

    a -2 -5 0
    a 2 -5 0
    a -2 5 0
    a 2 5 0

ASCII, ``\\n`` only, one trailing newline - the same byte discipline as
``dimacs.py``, and for the same reason: these bytes are hashed.

**Cube instance**: the base CNF with the cube's literals appended as unit
clauses and the header clause count raised by ``s``. ``write_cube_cnf`` is the
one implementation of that; a test pins it against ``dimacs.write_cnf`` fed the
base clauses followed by the same units, so the "append and bump the header"
rule cannot drift from the writer that produced the base.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

CONSTRUCTION = "mask-lsb-first.v1"

# 2**24 cubes is 16.7 million lines. Nothing this repo does needs that, and a
# manifest asking for it is far likelier to be corrupt than ambitious, so the
# guard fails closed instead of hashing for an hour.
MAX_SPLIT = 24

_CHUNK = 1 << 20
_HEADER = re.compile(r"^p cnf (\d+) (\d+)\n")


class CubeError(ValueError):
    """The split, the cube index or the base instance is not usable."""


def check_split(split_vars: Sequence[int], n_main: int | None = None) -> None:
    """Raise unless ``split_vars`` is a usable split, optionally within ``1..n_main``."""
    if not isinstance(split_vars, (list, tuple)):
        raise CubeError(f"split_vars must be a list, got {type(split_vars).__name__}")
    if not split_vars:
        raise CubeError("split_vars is empty; a wave with no split is not a decomposition")
    if len(split_vars) > MAX_SPLIT:
        raise CubeError(f"split of {len(split_vars)} variables exceeds the ceiling {MAX_SPLIT}")
    for v in split_vars:
        if isinstance(v, bool) or not isinstance(v, int):
            raise CubeError(f"split variable {v!r} is not an int")
        if v < 1:
            raise CubeError(f"split variable {v} is not a positive variable number")
        if n_main is not None and v > n_main:
            raise CubeError(
                f"split variable {v} is above {n_main}; a wave splits on main variables, "
                "whose numbering is var(x_n) = n"
            )
    if len(set(split_vars)) != len(split_vars):
        raise CubeError("split_vars repeats a variable; the cubes would not be a partition")


def n_cubes(split_vars: Sequence[int]) -> int:
    check_split(split_vars)
    return 1 << len(split_vars)


def cube_literals(split_vars: Sequence[int], index: int) -> list[int]:
    """The literals of cube ``index``: bit ``j`` set means ``+split_vars[j]``."""
    check_split(split_vars)
    if isinstance(index, bool) or not isinstance(index, int):
        raise CubeError(f"cube index {index!r} is not an int")
    if not 0 <= index < (1 << len(split_vars)):
        raise CubeError(f"cube index {index} is outside 0..{(1 << len(split_vars)) - 1}")
    return [v if (index >> j) & 1 else -v for j, v in enumerate(split_vars)]


def cube_clauses(split_vars: Sequence[int], index: int) -> list[list[int]]:
    """Cube ``index`` as unit clauses, in the order they are appended to the base."""
    return [[lit] for lit in cube_literals(split_vars, index)]


def iter_cube_lines(split_vars: Sequence[int]) -> Iterator[str]:
    """Every cube of the split, in index order, as one cube-file line each."""
    check_split(split_vars)
    for index in range(1 << len(split_vars)):
        lits = [v if (index >> j) & 1 else -v for j, v in enumerate(split_vars)]
        yield "a " + " ".join(str(lit) for lit in lits) + " 0\n"


def cubes_text(split_vars: Sequence[int]) -> str:
    return "".join(iter_cube_lines(split_vars))


def cubes_sha256(split_vars: Sequence[int]) -> str:
    """sha256 of the cube file this split defines, without materialising it."""
    digest = hashlib.sha256()
    for line in iter_cube_lines(split_vars):
        digest.update(line.encode("ascii"))
    return digest.hexdigest()


def write_cube_cnf(
    base_cnf: str | os.PathLike[str],
    split_vars: Sequence[int],
    index: int,
    out_path: str | os.PathLike[str],
) -> dict[str, object]:
    """Write the base instance plus cube ``index``; return ``{path, n_vars, n_clauses, sha256}``.

    The header clause count goes up by one per split variable and the units are
    appended in construction order. Nothing else about the base changes, so the
    body bytes of the two files are identical.
    """
    lits = cube_literals(split_vars, index)
    source = Path(base_cnf)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    with open(source, "rb") as body_in, open(out, "wb") as final:
        first = body_in.readline().decode("ascii", errors="replace")
        found = _HEADER.match(first)
        if not found:
            raise CubeError(f"{source} does not start with a 'p cnf <vars> <clauses>' line")
        n_vars, n_clauses = int(found.group(1)), int(found.group(2))
        for lit in lits:
            if abs(lit) > n_vars:
                raise CubeError(
                    f"split variable {abs(lit)} is outside the base instance's 1..{n_vars}"
                )
        header = f"p cnf {n_vars} {n_clauses + len(lits)}\n".encode("ascii")
        final.write(header)
        digest.update(header)
        while chunk := body_in.read(_CHUNK):
            final.write(chunk)
            digest.update(chunk)
        tail = "".join(f"{lit} 0\n" for lit in lits).encode("ascii")
        final.write(tail)
        digest.update(tail)

    return {
        "path": str(out),
        "n_vars": n_vars,
        "n_clauses": n_clauses + len(lits),
        "sha256": digest.hexdigest(),
    }
