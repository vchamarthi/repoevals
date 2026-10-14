"""The run matrix: prepare → agent → grade for every (task, arm, repeat), robustly and optionally in parallel.

- Hardware requirements are checked before the agent runs; unsupported tasks are recorded as `skipped` at no cost.
- Agent infrastructure failures (missing CLI, authentication, rate limits) are retried with backoff and otherwise
  recorded as `error`, never as a failure of the agent.
- Any exception in one attempt is recorded as `error` for that attempt; the rest of the matrix continues.
- `resume` skips attempts that already have a non-error grade, so an interrupted run can be continued.
- `max_cost` stops scheduling new attempts once the reported cost exceeds the budget.
- `<out>/run.json` records the arguments, versions, source revision and outcome of the whole run.
"""
from __future__ import annotations

import json
import os
import platform
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .config import RepoConfig
from .task import Task, host_supports
from .util import git, package_versions, within


def default_runs_root(cfg: RepoConfig) -> Path:
    base = os.environ.get("REPOEVALS_RUNS_DIR") or str(
        Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "repoevals" / "runs")
    return Path(base).expanduser() / cfg.id


def arm_dirname(arm: str) -> str:
    return arm.replace(":", "_").replace("/", "_").replace("=", "_")


@dataclass
class RunOptions:
    agent: str
    arms: list[str]
    repeats: int = 3
    model: str = ""
    docker: str = ""
    agent_args: str = ""
    jobs: int = 1
    resume: bool = False
    retries: int = 2
    max_cost: float | None = None
    label: str = ""
    shuffle: bool = False
    seed: int = 0
    log: list = field(default_factory=list)


def _write_grade(rd: Path, task: Task, status: str, errors: list[str], opts: RunOptions, repeat: int,
                 arm: str) -> dict:
    g = {"schema": "repoevals.grade/v1", "task": task.id, "family": task.family, "grader": task.grader_type,
         "arm": arm, "agent": opts.agent, "model": opts.model or None, "label": opts.label or None,
         "repeat": repeat, "status": status, "pass": None, "metrics": {}, "details": {}, "process": {},
         "integrity": {}, "errors": errors, "created": str(task.metadata.get("created", "")),
         "repoevals_version": __version__, "graded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    rd.mkdir(parents=True, exist_ok=True)
    meta = rd / "meta.json"
    if not meta.exists():
        meta.write_text(json.dumps({"task": task.id, "task_dir": str(task.dir), "arm": arm, "agent": opts.agent,
                                    "repeat": repeat, "label": opts.label or None}, indent=1))
    (rd / "grade.json").write_text(json.dumps(g, indent=1))
    return g


def run_matrix(cfg: RepoConfig, tasks: list[Task], source: Path, out: Path, opts: RunOptions,
               progress=print) -> dict:
    from .agents import agent_version, record, run_agent
    from .grade import grade_run
    from .workspace import prepare, update_meta

    out.mkdir(parents=True, exist_ok=True)
    version = agent_version(opts.agent)
    jobs = [(t, arm, rep) for t in tasks for arm in opts.arms for rep in range(opts.repeats)]
    if opts.shuffle:
        random.Random(opts.seed).shuffle(jobs)
    manifest = {"schema": "repoevals.run/v1", "repoevals_version": __version__, "agent": opts.agent,
                "agent_version": version or None, "model": opts.model or None, "label": opts.label or None,
                "arms": opts.arms, "repeats": opts.repeats, "tasks": [t.id for t in tasks],
                "source": str(source), "source_sha": git(source, "rev-parse", "HEAD").out.strip(),
                "host": {"platform": platform.platform(), "python": platform.python_version(),
                         "cpus": os.cpu_count()},
                "packages": package_versions(),
                "docker": opts.docker or None, "jobs": opts.jobs, "max_cost": opts.max_cost,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (out / "run.json").write_text(json.dumps(manifest, indent=1))
    lock = threading.Lock()
    state = {"cost": 0.0, "stopped": "", "counts": {}, "warned": False}
    env_pass = list(cfg.environment.get("env_passthrough", [])) or None
    network = str(cfg.environment.get("network", "bridge"))

    def attempt(t: Task, arm: str, rep: int) -> dict:
        rd = out / t.id / arm_dirname(arm) / f"r{rep}"
        if opts.resume and (rd / "grade.json").exists():
            try:
                old = json.loads((rd / "grade.json").read_text())
                if old.get("status") != "error":
                    return old
            except ValueError:
                pass
        with lock:
            if state["stopped"]:
                return {"task": t.id, "status": "not_run"}
        ok, why = host_supports(t)
        if not ok:
            return _write_grade(rd, t, "skipped", [f"skipped: {why}"], opts, rep, arm)
        try:
            res, attempts = None, 0
            for attempts in range(1, opts.retries + 2):
                meta = prepare(cfg, t, rd, source, arm)
                with lock:
                    if meta.get("ancestor_guidance") and not state["warned"]:
                        state["warned"] = True
                        progress("warning: guidance outside the workspace (parent directories or user agent config) "
                                 "applies to every arm: " + ", ".join(meta["ancestor_guidance"]))
                res = run_agent(opts.agent, rd, model=opts.model, timeout=float(t.agent.get("timeout_sec", 3600)),
                                docker=opts.docker, network=network, extra_args=opts.agent_args,
                                env_passthrough=env_pass)
                if res.status != "infra_error" or attempts > opts.retries:
                    break
                time.sleep(min(300, 15 * 2 ** (attempts - 1)))
            record(rd, opts.agent, opts.model, res, attempts=attempts, version=version)
            update_meta(rd, repeat=rep, label=opts.label or None)
            g = grade_run(rd, cfg, t)
        except Exception as e:  # one broken attempt must not stop the matrix
            g = _write_grade(rd, t, "error", [f"{type(e).__name__}: {e}"], opts, rep, arm)
        cost = (g.get("process") or {}).get("cost_usd") or 0.0
        with lock:
            state["cost"] += cost
            if opts.max_cost is not None and state["cost"] >= opts.max_cost and not state["stopped"]:
                state["stopped"] = f"cost budget of ${opts.max_cost} reached"
        return g

    total = len(jobs)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, opts.jobs)) as pool:
        futs = {pool.submit(attempt, *j): j for j in jobs}
        for f in as_completed(futs):
            t, arm, rep = futs[f]
            g = f.result()
            done += 1
            st = g.get("status", "error")
            state["counts"][st] = state["counts"].get(st, 0) + 1
            secs = ""
            if st != "not_run":
                meta_p = out / t.id / arm_dirname(arm) / f"r{rep}" / "meta.json"
                try:
                    secs = f"{json.loads(meta_p.read_text()).get('agent_seconds') or 0:7.1f}s"
                except (OSError, ValueError):
                    secs = ""
            progress(f"[{done}/{total}] {t.id:36s} {arm:20s} r{rep} {st:8s} {secs}")
    manifest.update(finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), counts=state["counts"],
                    cost_usd=round(state["cost"], 4), stopped_reason=state["stopped"] or None)
    (out / "run.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def check_out_dir(out: Path, cfg: RepoConfig, source: Path) -> str:
    """Why `out` is a bad place for run directories ('' if fine)."""
    for root in {cfg.root.resolve(), source.resolve()}:
        if within(root, out):
            return (f"{out} is inside the repository {root}: agents could read the source checkout (including "
                    "hidden tests and the reference solution) and its guidance files")
    return ""
