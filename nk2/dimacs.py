"""Deterministic DIMACS CNF writer.

Every instance this repo claims anything about is identified by the sha256 of
these bytes and regenerated at gate time, so the byte format is a contract:

* ASCII, ``\\n`` line endings only - never CRLF, which is the live bug on the
  development machine and the reason ``.gitattributes`` pins ``eol=lf``;
* one ``p cnf <n_vars> <n_clauses>`` header line, no comment lines;
* one clause per line, literals space-separated **in emission order, not
  sorted**, terminated by `` 0``;
* exactly one trailing newline.

The clause count is not known until the iterator is drained and the header has
to come first, so the body streams to a sibling temp file and is copied under
the header afterwards. Sibling, not the system temp directory, so the copy stays
on one filesystem.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

_CHUNK = 1 << 20


class DimacsError(ValueError):
    """The clause stream cannot be written as valid DIMACS."""


def write_cnf(
    path: str | os.PathLike[str],
    n_vars: int,
    clause_iter: Iterable[Sequence[int]],
) -> dict[str, object]:
    """Write ``clause_iter`` to ``path``; return ``{path, n_vars, n_clauses, sha256}``."""
    if isinstance(n_vars, bool) or not isinstance(n_vars, int) or n_vars < 0:
        raise DimacsError(f"n_vars must be a non-negative int, got {n_vars!r}")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".body.tmp")

    n_clauses = 0
    try:
        with open(tmp, "wb") as body:
            for clause in clause_iter:
                parts = []
                for lit in clause:
                    if isinstance(lit, bool) or not isinstance(lit, int):
                        raise DimacsError(f"literal must be an int, got {lit!r}")
                    if lit == 0:
                        raise DimacsError("0 terminates a clause and cannot be a literal")
                    if -n_vars <= lit <= n_vars:
                        parts.append(str(lit))
                    else:
                        raise DimacsError(
                            f"literal {lit} is outside the declared range 1..{n_vars}; "
                            "the header is not counting every variable"
                        )
                parts.append("0")
                body.write((" ".join(parts) + "\n").encode("ascii"))
                n_clauses += 1

        digest = hashlib.sha256()
        header = f"p cnf {n_vars} {n_clauses}\n".encode("ascii")
        with open(out, "wb") as final, open(tmp, "rb") as body_in:
            final.write(header)
            digest.update(header)
            while chunk := body_in.read(_CHUNK):
                final.write(chunk)
                digest.update(chunk)
    finally:
        if tmp.exists():
            tmp.unlink()

    return {
        "path": str(out),
        "n_vars": n_vars,
        "n_clauses": n_clauses,
        "sha256": digest.hexdigest(),
    }
