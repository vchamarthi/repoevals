"""Graders for scientific libraries: numerical accuracy against a trusted reference, and performance.

type = "numeric"
    probe = "probe.py"                 # in the task dir (see heldout/data/numeric_runner.py for the contract)
    python = "python"                  # interpreter of the repository's environment
    tolerances = { float32 = [1e-5, 1e-6], float64 = [1e-12, 1e-14] }   # [rtol, atol] per dtype
    min_case_pass = 1.0                # fraction of cases that must be within tolerance
    targets = [...]                    # optional: tests (via the repo adapter) that must also pass
    The probe runs on the agent's tree with hidden/ overlaid, so tasks can pair hidden tests with probes.

type = "perf"
    bench = "python bench.py"          # in the task dir; prints a JSON line {"seconds": float} (or is timed)
    repeats = 7                        # interleaved base/candidate runs
    min_speedup = 1.2                  # pass if the lower bound of the bootstrap CI of the speedup >= this
    targets = [...]                    # correctness gate via the repo adapter (hidden tests allowed)
    The baseline is the starting commit, built in a separate copy, measured interleaved with the candidate
    to cancel machine drift. Speedup = median(base) / median(candidate). Each run gets PYTHONPATH=<its tree>;
    for compiled extensions use an in-tree build ([test].build = "... build_ext --inplace"), because an editable
    install points at whichever tree was built last.
"""
from __future__ import annotations

import json
import random
import shutil
import statistics
import tempfile
from pathlib import Path

from ..util import copytree_overlay, run_shell
from . import GradeContext
from .tests import TreeGuard, apply_reference

RUNNER = Path(__file__).resolve().parent.parent / "data" / "numeric_runner.py"


def _correctness(ctx: GradeContext, out: dict) -> bool:
    adapter = ctx.adapter()
    hidden = ctx.task.dir / ctx.g.get("hidden_dir", "hidden")
    if hidden.is_dir():
        out["hidden_files"] = copytree_overlay(hidden, ctx.workspace)
    built = adapter.build(ctx.workspace)
    if built is not None and not built.ok:
        out["build_ok"] = False
        out["build_log_tail"] = built.out[-3000:]
        return False
    out["build_ok"] = True
    if ctx.g.get("targets"):
        res = adapter.run(ctx.workspace, list(ctx.g["targets"]), build=False)
        out["tests"] = res.summary()
        out["tests_pass"] = res.ok
        return res.ok
    return True


def _ws_env(ctx: GradeContext, tree: Path) -> dict:
    """Test env plus PYTHONPATH=<tree>, so Python code is imported from the tree being measured."""
    import os
    env = dict(ctx.config.test.env)
    if ctx.g.get("pythonpath_workspace", True):
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(tree), env.get("PYTHONPATH",
                                                                            os.environ.get("PYTHONPATH", ""))]))
    return env


def g_numeric(ctx: GradeContext) -> dict:
    g, ws = ctx.g, ctx.workspace
    out: dict = {}
    py = g.get("python", "python")
    timeout = ctx.task.verifier.get("timeout_sec", 1800)
    probe = ctx.task.dir / g.get("probe", "probe.py")
    guard = TreeGuard(ws)
    with tempfile.TemporaryDirectory() as td:
        cand, ref, res_path = Path(td) / "cand.npz", Path(td) / "ref.npz", Path(td) / "numeric.json"
        try:
            tests_ok = _correctness(ctx, out)
            # 1. candidate: the agent's code, in its own process
            p = run_shell(f"{py} {RUNNER} candidate {probe} {cand}", cwd=ws, timeout=timeout, env=_ws_env(ctx, ws))
            out["candidate_rc"] = p.rc
            if not cand.exists() or not Path(str(cand) + ".errors.json").exists():
                out["candidate_log_tail"] = p.out[-3000:]
        finally:
            guard.restore()
        # 2. reference, 3. comparison: trusted code only, not run from the workspace and without it on the path
        r = run_shell(f"{py} -I {RUNNER} reference {probe} {ref}", cwd=td, timeout=timeout)
        if not ref.exists():
            raise RuntimeError(f"numeric reference failed (rc={r.rc}): {r.out[-3000:]}")
        cand_arg = str(cand) if cand.exists() and Path(str(cand) + ".errors.json").exists() else "-"
        tol = json.dumps(g.get("tolerances", {}))
        c = run_shell(f"{py} -I {RUNNER} compare {probe} {cand_arg} {ref} {res_path} '{tol}'", cwd=td,
                      timeout=timeout)
        if not res_path.exists():
            raise RuntimeError(f"numeric comparison failed (rc={c.rc}): {c.out[-3000:]}")
        cases = json.loads(res_path.read_text())
    n_ok = sum(bool(c.get("ok")) for c in cases)
    out.update(cases=cases, n_cases=len(cases), n_cases_ok=n_ok,
               case_pass_rate=round(n_ok / len(cases), 3) if cases else None,
               max_ulp=max((c.get("max_ulp") or 0 for c in cases), default=0),
               numeric_ok=bool(cases) and n_ok / len(cases) >= float(g.get("min_case_pass", 1.0)))
    out["pass"] = bool(out["numeric_ok"] and tests_ok and out.get("build_ok", True))
    return out


def _time(cmd: str, cwd: Path, env: dict, timeout: float) -> float | None:
    p = run_shell(cmd, cwd=cwd, timeout=timeout, env=env)
    if not p.ok:
        return None
    for line in reversed(p.out.strip().splitlines()):
        try:
            v = json.loads(line)
            if isinstance(v, dict) and "seconds" in v:
                return float(v["seconds"])
        except ValueError:
            continue
    return p.seconds


def export_tree(gitdir: Path, rev: str, dest: Path) -> None:
    """Check out `rev` from gitdir into dest without touching any existing work tree or index."""
    from ..util import run
    with tempfile.TemporaryDirectory() as td:
        env = {"GIT_INDEX_FILE": str(Path(td) / "index")}
        for args in (["read-tree", rev], ["checkout-index", "-a", "-f"]):
            p = run(["git", f"--git-dir={gitdir}", f"--work-tree={dest}", *args], cwd=dest, env=env)
            if p.rc != 0:
                raise RuntimeError(f"git {args[0]} failed: {p.out[:500]}")


def bootstrap_ratio_ci(base: list[float], cand: list[float], n: int = 2000, alpha: float = 0.05,
                       seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    ratios = sorted(statistics.median(rng.choices(base, k=len(base))) /
                    statistics.median(rng.choices(cand, k=len(cand))) for _ in range(n))
    return ratios[int(n * alpha / 2)], ratios[int(n * (1 - alpha / 2)) - 1]


def g_perf(ctx: GradeContext) -> dict:
    g, ws = ctx.g, ctx.workspace
    out: dict = {}
    guard = TreeGuard(ws)
    base_dir = Path(tempfile.mkdtemp(prefix="heldout-base-"))
    try:
        if not _correctness(ctx, out):
            out["pass"] = False
            return out
        # baseline tree = starting commit (+ hidden files so the bench sees the same harness)
        bt = base_dir / "t"
        bt.mkdir()
        export_tree(ctx.tree().gitdir, ctx.start_sha, bt)
        hidden = ctx.task.dir / g.get("hidden_dir", "hidden")
        if hidden.is_dir():
            copytree_overlay(hidden, bt)
        b = ctx.adapter().build(bt)
        if b is not None and not b.ok:
            raise RuntimeError(f"baseline does not build: {b.out[-2000:]}")
        bench = g.get("bench", "python bench.py")
        bench_path = ctx.task.dir / bench.split()[-1]
        if bench_path.exists():
            bench = bench.replace(bench.split()[-1], str(bench_path))
        timeout = float(g.get("bench_timeout_sec", 600))
        envs = {bt: _ws_env(ctx, bt), ws: _ws_env(ctx, ws)}
        base_t, cand_t = [], []
        for _ in range(int(g.get("warmup", 1))):
            _time(bench, bt, envs[bt], timeout), _time(bench, ws, envs[ws], timeout)
        for _ in range(int(g.get("repeats", 7))):
            for tree, acc in ((bt, base_t), (ws, cand_t)):
                v = _time(bench, tree, envs[tree], timeout)
                if v is not None:
                    acc.append(v)
        if len(base_t) < 3 or len(cand_t) < 3:
            raise RuntimeError(f"benchmark failed too often (base {len(base_t)}, candidate {len(cand_t)} runs)")
        lo, hi = bootstrap_ratio_ci(base_t, cand_t)
        out.update(base_seconds=base_t, candidate_seconds=cand_t,
                   speedup=round(statistics.median(base_t) / statistics.median(cand_t), 3),
                   speedup_ci=[round(lo, 3), round(hi, 3)],
                   noise_cv_base=round(statistics.pstdev(base_t) / statistics.mean(base_t), 3))
        out["pass"] = lo >= float(g.get("min_speedup", 1.1))
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)
        guard.restore()
    return out


def o_apply(ctx: GradeContext) -> None:
    apply_reference(ctx)
    return None


GRADERS = {"numeric": g_numeric, "perf": g_perf}
ORACLES = {"numeric": o_apply, "perf": o_apply}
