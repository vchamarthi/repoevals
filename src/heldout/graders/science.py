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
    to cancel machine drift. Speedup = median(base) / median(candidate).
"""
from __future__ import annotations

import json
import random
import shutil
import statistics
import tempfile
from pathlib import Path

from ..util import copytree_overlay, git, run_shell
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


def g_numeric(ctx: GradeContext) -> dict:
    g, ws = ctx.g, ctx.workspace
    out: dict = {}
    guard = TreeGuard(ws)
    try:
        tests_ok = _correctness(ctx, out)
        probe = ctx.task.dir / g.get("probe", "probe.py")
        with tempfile.TemporaryDirectory() as td:
            res_path = Path(td) / "numeric.json"
            tol = json.dumps(g.get("tolerances", {}))
            py = g.get("python", "python")
            env = dict(ctx.config.test.env)
            if g.get("pythonpath_workspace", True):  # import the agent's tree, not an installed copy
                import os
                env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ws), env.get("PYTHONPATH",
                                                                                  os.environ.get("PYTHONPATH", ""))]))
            p = run_shell(f"{py} {RUNNER} {probe} {res_path} '{tol}'", cwd=ws,
                          timeout=ctx.task.verifier.get("timeout_sec", 1800), env=env)
            if not res_path.exists():
                raise RuntimeError(f"numeric runner produced no results (rc={p.rc}): {p.out[-3000:]}")
            cases = json.loads(res_path.read_text())
    finally:
        guard.restore()
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
        git(ws, "worktree", "add", "--detach", str(base_dir / "t"), ctx.start_sha, check=True)
        bt = base_dir / "t"
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
        env, timeout = ctx.config.test.env, float(g.get("bench_timeout_sec", 600))
        base_t, cand_t = [], []
        for _ in range(int(g.get("warmup", 1))):
            _time(bench, bt, env, timeout), _time(bench, ws, env, timeout)
        for _ in range(int(g.get("repeats", 7))):
            for which, tree, acc in (("b", bt, base_t), ("c", ws, cand_t)):
                v = _time(bench, tree, env, timeout)
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
        git(ws, "worktree", "remove", "--force", str(base_dir / "t"))
        shutil.rmtree(base_dir, ignore_errors=True)
        guard.restore()
    return out


def o_apply(ctx: GradeContext) -> None:
    apply_reference(ctx)
    return None


GRADERS = {"numeric": g_numeric, "perf": g_perf}
ORACLES = {"numeric": o_apply, "perf": o_apply}
