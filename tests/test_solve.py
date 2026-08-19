"""The driver. The single rule under test is that the return code is the verdict.

A stub solver stands in for the real one so that the disagreement cases - text
saying one thing, rc saying another - can actually be produced. They cannot be
produced on demand with a correct solver, which is exactly why they go untested
otherwise and why M17 survives in most projects.
"""

import json
import os
import subprocess
import sys

import pytest

from nk2 import encode_seqcount
from nk2.evaluator import avoids
from nk2.solve import (
    RUNLOG_SCHEMA,
    SolverIntegrityError,
    build_instance,
    coloring_from_model,
    find_solvers,
    repo_root,
    resolve_under_root,
    solve,
)

STUB = '''\
import sys

if "--version" in sys.argv:
    print("stub 0.0")
    raise SystemExit(0)
print({status!r})
{extra}
raise SystemExit({rc})
'''


@pytest.fixture
def stub(tmp_path):
    def make(status: str, rc: int, extra: str = "") -> list[str]:
        path = tmp_path / f"stub_{abs(hash((status, rc, extra)))}.py"
        path.write_text(STUB.format(status=status, rc=rc, extra=extra), encoding="ascii")
        return [sys.executable, str(path)]

    return make


@pytest.fixture
def instance(tmp_path):
    return build_instance(9, 3, 2, "seqcount", tmp_path / "i.cnf")


def test_exit_zero_with_unsat_text_is_unknown(stub, instance, tmp_path):
    # M17: inferring the verdict from stdout would report UNSAT here. A solver
    # that was killed after printing its answer looks exactly like this.
    log = solve(instance, stub("s UNSATISFIABLE", 0), 60, root=tmp_path)
    assert (log.rc, log.verdict) == (0, "UNKNOWN")
    assert log.model is None


def test_exit_one_is_unknown(stub, instance, tmp_path):
    log = solve(instance, stub("s SATISFIABLE", 1), 60, root=tmp_path)
    assert (log.rc, log.verdict) == (1, "UNKNOWN")


def test_rc_ten_with_unsat_text_raises(stub, instance, tmp_path):
    with pytest.raises(SolverIntegrityError):
        solve(instance, stub("s UNSATISFIABLE", 10), 60, root=tmp_path)


def test_rc_twenty_with_sat_text_raises(stub, instance, tmp_path):
    with pytest.raises(SolverIntegrityError):
        solve(instance, stub("s SATISFIABLE", 20), 60, root=tmp_path)


def test_rc_twenty_is_unsat(stub, instance, tmp_path):
    log = solve(instance, stub("s UNSATISFIABLE", 20), 60, root=tmp_path)
    assert log.verdict == "UNSAT"
    assert log.rc == 20
    assert log.timed_out is False


def test_rc_ten_parses_the_model_from_v_lines(stub, instance, tmp_path):
    model = " ".join(str(n if n % 3 else -n) for n in range(1, 138))
    log = solve(
        instance,
        stub("s SATISFIABLE", 10, extra=f'print("v {model} 0")'),
        60,
        root=tmp_path,
    )
    assert log.verdict == "SAT"
    assert log.model is not None
    coloring = coloring_from_model(log.model, 9)
    assert coloring == [1 if n % 3 else -1 for n in range(1, 10)]


def test_a_model_missing_positions_is_refused(stub, instance, tmp_path):
    log = solve(instance, stub("s SATISFIABLE", 10, extra='print("v 1 -2 0")'), 60, root=tmp_path)
    with pytest.raises(SolverIntegrityError):
        coloring_from_model(log.model or [], 9)


def test_timeout_records_rc_none_and_unknown(stub, instance, tmp_path):
    log = solve(instance, stub("s SATISFIABLE", 10, extra="import time; time.sleep(30)"), 0.5,
                root=tmp_path)
    assert log.rc is None
    assert log.verdict == "UNKNOWN"
    assert log.timed_out is True


def test_run_log_shape_and_no_absolute_paths(stub, instance, tmp_path):
    log = solve(instance, stub("s UNSATISFIABLE", 20), 60, root=tmp_path)
    path = log.write(tmp_path / "run.json", root=tmp_path)
    data = json.loads(path.read_text(encoding="ascii"))
    assert data["schema"] == RUNLOG_SCHEMA
    assert set(data) == {
        "schema", "instance", "solver", "args", "rc", "verdict", "timed_out",
        "wall_seconds", "proof", "host", "started_utc", "finished_utc",
    }
    assert set(data["instance"]) == {
        "path_rel", "sha256", "n_vars", "n_clauses", "N", "k", "l", "encoder",
        "symmetry_break",
    }
    assert set(data["solver"]) == {"name", "version", "exe_sha256"}
    assert set(data["host"]) == {"os", "python"}
    assert data["instance"]["path_rel"] == "i.cnf"
    assert data["proof"] is None

    raw = path.read_text(encoding="ascii")
    assert "\\" not in data["instance"]["path_rel"]
    assert all(not os.path.isabs(arg) for arg in data["args"])
    for fragment in (str(tmp_path), sys.executable, "/home/", "/Users/"):
        assert fragment not in raw
    assert raw.isascii()


def test_run_log_host_carries_no_identity(stub, instance, tmp_path):
    import platform

    log = solve(instance, stub("s UNSATISFIABLE", 20), 60, root=tmp_path)
    assert log.host["os"] == f"{platform.system()} {platform.release()}".strip()
    assert log.host["python"] == platform.python_version()
    assert platform.node() not in json.dumps(log.to_dict(tmp_path))


def test_find_solvers_prefers_the_env_dir(tmp_path):
    fake = tmp_path / "kissat.exe" if os.name == "nt" else tmp_path / "kissat"
    fake.write_bytes(b"")
    found = find_solvers({"NK2_SOLVER_DIR": str(tmp_path), "PATH": ""})
    assert found["kissat"] == str(fake)
    assert "cadical" not in found


def test_find_solvers_returns_nothing_when_there_is_nothing(tmp_path):
    assert find_solvers({"NK2_SOLVER_DIR": str(tmp_path / "nope"), "PATH": ""}) == {}


def test_resolve_under_root_refuses_an_escape():
    assert resolve_under_root("evidence/runs/x.json").is_relative_to(repo_root())
    for bad in ("../outside.json", "evidence/../../outside.json"):
        with pytest.raises(ValueError):
            resolve_under_root(bad)


def test_build_instance_matches_the_encoder(tmp_path):
    inst = build_instance(9, 3, 2, "seqcount", tmp_path / "a.cnf")
    assert (inst.n_vars, inst.n_clauses) == (137, 256)
    assert inst.encoder == "seqcount"
    assert inst.symmetry_break is False
    assert inst.n_vars == encode_seqcount.num_vars(9, 3, 2)
    with pytest.raises(KeyError):
        build_instance(9, 3, 2, "nope", tmp_path / "b.cnf")


# --- real solver ------------------------------------------------------------

def available():
    return find_solvers()


@pytest.mark.solver
@pytest.mark.skipif(not available(), reason="no external solver on this machine")
@pytest.mark.parametrize("encoder", ["subsets", "seqcount", "totalizer"])
def test_real_solver_agrees_with_the_k3_anchor(encoder, tmp_path):
    name, exe = sorted(available().items())[0]
    root = repo_root()
    sat = build_instance(8, 3, 2, encoder, root / "evidence" / "instances" / f"t8_{encoder}.cnf")
    unsat = build_instance(9, 3, 2, encoder, root / "evidence" / "instances" / f"t9_{encoder}.cnf")
    try:
        sat_log = solve(sat, exe, 120)
        assert (sat_log.rc, sat_log.verdict) == (10, "SAT"), name
        assert avoids(coloring_from_model(sat_log.model or [], 8), 3, 2)
        unsat_log = solve(unsat, exe, 120)
        assert (unsat_log.rc, unsat_log.verdict) == (20, "UNSAT"), name
    finally:
        for path in (sat.path, unsat.path):
            path.unlink(missing_ok=True)


@pytest.mark.solver
@pytest.mark.skipif(not available(), reason="no external solver on this machine")
def test_cli_runs_end_to_end(tmp_path):
    root = repo_root()
    out = subprocess.run(
        [sys.executable, "-m", "nk2.solve", "--N", "8", "--k", "3", "--encoder", "seqcount",
         "--cnf", "evidence/instances/cli_test.cnf",
         "--run-log", "evidence/runs/cli_test.json",
         "--witness", "evidence/instances/cli_test_witness.txt"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    try:
        assert out.returncode == 0, out.stderr
        assert "rc=10 verdict=SAT" in out.stdout
    finally:
        for name in ("instances/cli_test.cnf", "runs/cli_test.json",
                     "instances/cli_test_witness.txt"):
            (root / "evidence" / name).unlink(missing_ok=True)
