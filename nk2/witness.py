"""Witness text I/O.

Format, adopted from the published certificate bundles so artifacts interoperate:
zero or more ``#`` comment lines, then exactly one data line of ``+`` and ``-``,
one character per position, ``1``-indexed.

The reader is deliberately unforgiving. A reader that coerced an unrecognised
character to ``-`` would let a corrupted file parse as a *different* coloring,
and the gate would then verify that other coloring and report success.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from nk2.evaluator import avoids


class WitnessFormatError(ValueError):
    """The file on disk is not a witness in the format above."""


class WitnessVerificationError(ValueError):
    """The coloring does not avoid ``(k,l)``, so it is not a witness at all."""


def witness_header(k: int, l: int, N: int) -> list[str]:
    """The comment lines ``write_witness`` emits, as plain text without ``#``."""
    return [
        "N(k,l) avoidance witness",
        f"k = {k}",
        f"l = {l}",
        f"N = {N}",
        "data line: N characters, position n (1-indexed) is + for f(n)=+1, - for f(n)=-1",
    ]


def read_witness(path: str | os.PathLike[str]) -> tuple[list[int], list[str]]:
    """Parse a witness file into ``(coloring, comments)``.

    ``comments`` is every comment line in order, with the leading ``#`` and one
    optional following space removed.
    """
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise WitnessFormatError(f"{path}: witness files are ASCII: {exc}") from exc

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    # A CRLF checkout is caught by the sha256 comparison in gate rule G2, not
    # here; tolerating the trailing CR keeps the parser interoperable.
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in lines]

    comments: list[str] = []
    data: str | None = None
    for i, line in enumerate(lines):
        if data is not None:
            raise WitnessFormatError(f"{path}: line {i + 1}: content after the data line")
        if line.startswith("#"):
            body = line[1:]
            comments.append(body[1:] if body.startswith(" ") else body)
            continue
        if line == "":
            raise WitnessFormatError(f"{path}: line {i + 1}: empty data line")
        bad = {c for c in line} - {"+", "-"}
        if bad:
            shown = " ".join(sorted(repr(c) for c in bad))
            raise WitnessFormatError(f"{path}: line {i + 1}: data line has {shown}, want + or -")
        data = line

    if data is None:
        raise WitnessFormatError(f"{path}: no data line")
    return [1 if c == "+" else -1 for c in data], comments


def write_witness(
    path: str | os.PathLike[str],
    f: list[int],
    k: int,
    l: int,
    comments: Sequence[str] = (),
    verify: bool = True,
) -> Path:
    """Write a witness, refusing by default to write a coloring that is not one.

    ``verify=False`` exists so that a rejected candidate can be dumped for
    inspection; nothing in the campaign path uses it.
    """
    for i, v in enumerate(f):
        if isinstance(v, bool) or not isinstance(v, int) or v not in (1, -1):
            raise ValueError(f"coloring[{i}] must be int +1 or -1, got {v!r}")
    extra = [str(c) for c in comments]
    for c in extra:
        if "\n" in c or "\r" in c:
            raise ValueError("comment lines may not contain a newline")
    if verify and not avoids(f, k, l):
        raise WitnessVerificationError(
            f"coloring of length {len(f)} does not avoid (k={k}, l={l}); refusing to write"
        )

    out = Path(path)
    lines = witness_header(k, l, len(f)) + extra
    body = "".join(("#\n" if c == "" else f"# {c}\n") for c in lines)
    body += "".join("+" if v == 1 else "-" for v in f) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body.encode("ascii"))
    return out
