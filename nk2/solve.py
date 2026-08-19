"""Subprocess solver driver, and the run-log that is the only record of a solve.

**The return code is the verdict.** ``rc == 10`` is SAT, ``rc == 20`` is UNSAT,
and everything else - 0, 1, a timeout, a signal, an out-of-memory kill - is
UNKNOWN. Solver text output is never the verdict: a truncated pipe with no
``s UNSATISFIABLE`` in it is indistinguishable from a crash, and a killed solver
that already printed its banner looks exactly like a solved one. If an ``s``
line contradicts a decisive return code, that is a broken solver or a broken
pipe and ``SolverIntegrityError`` is raised rather than a verdict recorded.

Nothing in a run-log is an absolute path: the instance is recorded
repo-relative, the solver as a bare file name, and ``host`` carries only the
platform string and the Python version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from nk2 import encode_seqcount, encode_subsets, encode_totalizer
from nk2.dimacs import write_cnf
from nk2.evaluator import avoids
from nk2.witness import write_witness

ENCODERS = {
    encode_subsets.NAME: encode_subsets,
    encode_seqcount.NAME: encode_seqcount,
    encode_totalizer.NAME: encode_totalizer,
}
SOLVER_NAMES = ("kissat", "cadical")

SAT, UNSAT, UNKNOWN = "SAT", "UNSAT", "UNKNOWN"
RUNLOG_SCHEMA = "nk2.runlog.v1"


class SolverIntegrityError(RuntimeError):
    """The solver's text output contradicts its return code."""


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_under_root(path: str | os.PathLike[str], root: Path | None = None) -> Path:
    """Resolve a path given on the command line and require it under the repo.

    Every CLI path crosses a trust boundary. Writes outside the repository are
    refused rather than sanitised, because a relative path with enough ``..`` in
    it is a legitimate-looking way to clobber something unrelated.
    """
    base = (root or repo_root()).resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = Path(os.path.normpath(candidate))
    if not candidate.is_relative_to(base):
        raise ValueError(f"path escapes the repository root: {path}")
    return candidate


def rel(path: Path, root: Path | None = None) -> str:
    """Repo-relative, forward-slashed, so a log written on Windows reads the same
    on Linux."""
    base = (root or repo_root()).resolve()
    return Path(path).resolve().relative_to(base).as_posix()


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def find_solvers(env: dict[str, str] | None = None) -> dict[str, str]:
    """Locate known solvers via ``NK2_SOLVER_DIR`` first, then ``PATH``.

    The environment variable is how a machine-specific solver directory stays
    out of every committed file.
    """
    environ = dict(os.environ if env is None else env)
    found: dict[str, str] = {}
    search = [d for d in environ.get("NK2_SOLVER_DIR", "").split(os.pathsep) if d]
    for name in SOLVER_NAMES:
        exe: Path | None = None
        for directory in search:
            for candidate in (Path(directory) / name, Path(directory) / f"{name}.exe"):
                if candidate.is_file():
                    exe = candidate
                    break
            if exe is not None:
                break
        if exe is None:
            which = shutil.which(name, path=environ.get("PATH"))
            if which:
                exe = Path(which)
        if exe is not None:
            found[name] = str(exe)
    return found


@dataclass(frozen=True)
class Instance:
    """A generated CNF and the parameters that regenerate it byte for byte."""

    path: Path
    sha256: str
    n_vars: int
    n_clauses: int
    N: int
    k: int
    l: int
    encoder: str
    symmetry_break: bool

    def to_dict(self, root: Path | None = None) -> dict[str, object]:
        return {
            "path_rel": rel(self.path, root),
            "sha256": self.sha256,
            "n_vars": self.n_vars,
            "n_clauses": self.n_clauses,
            "N": self.N,
            "k": self.k,
            "l": self.l,
            "encoder": self.encoder,
            "symmetry_break": self.symmetry_break,
        }


def build_instance(
    N: int,
    k: int,
    l: int,
    encoder: str,
    path: str | os.PathLike[str],
    symmetry_break: bool = False,
) -> Instance:
    """Generate the avoidance CNF for ``(N,k,l)`` with ``encoder``."""
    if encoder not in ENCODERS:
        raise KeyError(f"unknown encoder {encoder!r}; have {sorted(ENCODERS)}")
    module = ENCODERS[encoder]
    n_vars, clauses = module.build(N, k, l, symmetry_break=symmetry_break)
    info = write_cnf(path, n_vars, clauses)
    return Instance(
        path=Path(info["path"]),
        sha256=str(info["sha256"]),
        n_vars=int(info["n_vars"]),
        n_clauses=int(info["n_clauses"]),
        N=N,
        k=k,
        l=l,
        encoder=encoder,
        symmetry_break=symmetry_break,
    )


@dataclass
class RunLog:
    instance: Instance
    solver: dict[str, str]
    args: list[str]
    rc: int | None
    verdict: str
    timed_out: bool
    wall_seconds: float
    proof: dict[str, object] | None
    host: dict[str, str]
    started_utc: str
    finished_utc: str
    # Not serialised: a model is evidence only until it has been re-checked, and
    # a claim records the witness rather than the raw model.
    model: list[int] | None = field(default=None, repr=False)

    def to_dict(self, root: Path | None = None) -> dict[str, object]:
        return {
            "schema": RUNLOG_SCHEMA,
            "instance": self.instance.to_dict(root),
            "solver": self.solver,
            "args": self.args,
            "rc": self.rc,
            "verdict": self.verdict,
            "timed_out": self.timed_out,
            "wall_seconds": self.wall_seconds,
            "proof": self.proof,
            "host": self.host,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
        }

    def write(self, path: str | os.PathLike[str], root: Path | None = None) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(root), indent=2, sort_keys=True, ensure_ascii=True)
        out.write_bytes((text + "\n").encode("ascii"))
        return out


def _argv_prefix(solver: str | os.PathLike[str] | Sequence[str]) -> list[str]:
    if isinstance(solver, (str, os.PathLike)):
        return [str(solver)]
    return [str(part) for part in solver]


def _solver_version(prefix: Sequence[str]) -> str:
    try:
        proc = subprocess.run(
            [*prefix, "--version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    for line in proc.stdout.splitlines():
        if line.strip():
            return line.strip()
    return "unknown"


def _status_lines(stdout: str) -> set[str]:
    return {ln[2:].strip() for ln in stdout.splitlines() if ln.startswith("s ")}


def _parse_model(stdout: str, n_vars: int) -> list[int]:
    """Collect the ``v`` lines into a list of literals. Only ever called on rc 10."""
    literals: list[int] = []
    for line in stdout.splitlines():
        if not line.startswith("v "):
            continue
        for token in line[2:].split():
            value = int(token)
            if value == 0:
                return literals
            literals.append(value)
    return literals


def coloring_from_model(model: Sequence[int], N: int) -> list[int]:
    """Decode literals into ``f`` over ``{1..N}``. Works because ``var(x_n) = n``."""
    sign = {}
    for literal in model:
        variable = abs(literal)
        if variable <= N:
            sign[variable] = literal > 0
    missing = [n for n in range(1, N + 1) if n not in sign]
    if missing:
        raise SolverIntegrityError(f"model does not assign positions {missing[:5]}")
    return [1 if sign[n] else -1 for n in range(1, N + 1)]


def solve(
    cnf: Instance,
    solver: str | os.PathLike[str] | Sequence[str],
    timeout_s: float,
    proof: str | os.PathLike[str] | None = None,
    root: Path | None = None,
) -> RunLog:
    """Run ``solver`` on ``cnf`` and return the run-log. The rc is the verdict."""
    prefix = _argv_prefix(solver)
    argv = [*prefix]
    if proof is not None:
        # Text DRAT: drat-trim will not read kissat's binary format.
        argv.append("--no-binary")
    argv.append(str(cnf.path))
    if proof is not None:
        argv.append(str(proof))

    # The logged argv never carries an absolute path.
    logged = [Path(part).name if os.path.isabs(part) else part for part in prefix]
    if proof is not None:
        logged.append("--no-binary")
    logged.append(rel(cnf.path, root))
    if proof is not None:
        logged.append(rel(Path(proof), root))

    started = datetime.now(UTC)
    clock = time.monotonic()
    timed_out = False
    stdout = ""
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_s, check=False
        )
        rc: int | None = completed.returncode
        stdout = completed.stdout
    except subprocess.TimeoutExpired as expired:
        rc = None
        timed_out = True
        stdout = expired.stdout.decode("utf-8", "replace") if expired.stdout else ""
    wall = time.monotonic() - clock
    finished = datetime.now(UTC)

    verdict = {10: SAT, 20: UNSAT}.get(rc, UNKNOWN) if rc is not None else UNKNOWN
    statuses = _status_lines(stdout)
    if verdict == SAT and "UNSATISFIABLE" in statuses:
        raise SolverIntegrityError(f"rc 10 but the solver printed s UNSATISFIABLE ({argv[0]})")
    if verdict == UNSAT and "SATISFIABLE" in statuses:
        raise SolverIntegrityError(f"rc 20 but the solver printed s SATISFIABLE ({argv[0]})")

    model = _parse_model(stdout, cnf.n_vars) if verdict == SAT else None

    proof_record: dict[str, object] | None = None
    if proof is not None and Path(proof).exists():
        proof_record = {
            "path_rel": rel(Path(proof), root),
            "sha256": sha256_file(proof),
            "bytes": Path(proof).stat().st_size,
        }

    exe = Path(prefix[-1])
    return RunLog(
        instance=cnf,
        solver={
            "name": exe.stem,
            "version": _solver_version(prefix),
            "exe_sha256": sha256_file(exe) if exe.is_file() else "unknown",
        },
        args=logged,
        rc=rc,
        verdict=verdict,
        timed_out=timed_out,
        wall_seconds=round(wall, 3),
        proof=proof_record,
        host={
            # platform.system() + release only: no host name, no user name.
            "os": f"{platform.system()} {platform.release()}".strip(),
            "python": platform.python_version(),
        },
        started_utc=started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        finished_utc=finished.isoformat(timespec="seconds").replace("+00:00", "Z"),
        model=model,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nk2.solve",
        description="Generate an N(k,l) avoidance instance, solve it, and record a run-log.",
    )
    parser.add_argument("--N", type=int, required=True)
    parser.add_argument("--k", type=int, required=True)
    parser.add_argument("--l", type=int, default=2)
    parser.add_argument("--encoder", choices=sorted(ENCODERS), required=True)
    parser.add_argument("--solver", default=None, help="solver name or path; default: first found")
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--symmetry-break", action="store_true")
    parser.add_argument("--cnf", default=None, help="where to write the instance")
    parser.add_argument("--run-log", default=None, help="where to write the run-log JSON")
    parser.add_argument("--witness", default=None, help="where to write a witness, if SAT")
    parser.add_argument("--proof", default=None, help="where to write a DRAT proof")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = repo_root()
    tag = f"k{args.k}_l{args.l}_N{args.N}_{args.encoder}" + ("_sb" if args.symmetry_break else "")

    solvers = find_solvers()
    chosen = args.solver
    if chosen is None:
        if not solvers:
            print("no solver found; set NK2_SOLVER_DIR or put kissat on PATH", file=sys.stderr)
            return 2
        chosen = solvers[sorted(solvers)[0]]
    elif chosen in solvers:
        chosen = solvers[chosen]

    cnf_path = resolve_under_root(args.cnf or f"evidence/instances/{tag}.cnf", root)
    instance = build_instance(
        args.N, args.k, args.l, args.encoder, cnf_path, symmetry_break=args.symmetry_break
    )
    proof_path = resolve_under_root(args.proof, root) if args.proof else None
    if proof_path is not None:
        proof_path.parent.mkdir(parents=True, exist_ok=True)

    log = solve(instance, chosen, args.timeout, proof=proof_path, root=root)
    log_path = resolve_under_root(args.run_log or f"evidence/runs/{tag}.json", root)
    log.write(log_path, root)

    print(
        f"rc={log.rc} verdict={log.verdict} wall={log.wall_seconds}s "
        f"vars={instance.n_vars} clauses={instance.n_clauses} sha256={instance.sha256[:16]} "
        f"log={rel(log_path, root)}"
    )

    if log.verdict == SAT:
        coloring = coloring_from_model(log.model or [], args.N)
        # Never trust the solver's word for it: re-check with the evaluator
        # before anything is written as a witness.
        if not avoids(coloring, args.k, args.l):
            print("solver returned a model that does not avoid; refusing", file=sys.stderr)
            return 3
        if args.witness:
            path = write_witness(
                resolve_under_root(args.witness, root),
                coloring,
                args.k,
                args.l,
                comments=[f"found by {log.solver['name']} {log.solver['version']}"],
            )
            print(f"witness={rel(path, root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
