"""Grade one run directory and write `grade.json` (schema repoevals.grade/v1).

    {"schema": "repoevals.grade/v1", "task": "...", "family": "...", "grader": "...", "arm": "...",
     "agent": "...", "model": "...", "status": "pass|fail|skipped|error", "pass": true|false|null,
     "metrics": {...numbers/bools...}, "details": {...}, "process": {...trace metrics...},
     "integrity": {"integrity_ok": bool, "integrity_findings": [...]}, "errors": [...],
     "repoevals_version": "x.y.z", "graded_at": "..."}

A grader exception is recorded as status "error" (not graded), never as a failure: infrastructure problems must not
be counted against the agent. Regrading the same run directory gives the same result.
"""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from . import __version__
from .config import RepoConfig
from .graders import GRADERS, ORACLES, GradeContext, load_repo_graders
from .integrity import check as integrity_check
from .task import Task, host_supports, load_task
from .trace import process_metrics, read_events


def split_result(g: dict) -> tuple[dict, dict]:
    mets, det = {}, {}
    for k, v in g.items():
        if k == "pass":
            continue
        (mets if v is None or isinstance(v, (bool, int, float)) else det)[k] = v
    return mets, det


def context(run_dir: Path, cfg: RepoConfig, task: Task | None = None) -> GradeContext:
    meta = json.loads((run_dir / "meta.json").read_text())
    snapshot = run_dir / ".repoevals" / "tasks" / str(meta.get("task", ""))
    # grade from the copy of the task taken at prepare time, so later edits to the task cannot change old results
    task = task or load_task(snapshot if meta.get("task") and snapshot.is_dir() else Path(meta["task_dir"]))
    answer = run_dir / "answer.txt"
    backup = run_dir / ".repoevals" / "start.git"
    return GradeContext(run_dir=run_dir, workspace=(run_dir / "workspace").resolve(), task=task, config=cfg,
                        start_sha=meta["start_sha"], answer=answer.read_text() if answer.exists() else "",
                        events=read_events(run_dir), gitdir=backup if backup.is_dir() else None)


def grade_run(run_dir: Path, cfg: RepoConfig, task: Task | None = None) -> dict:
    load_repo_graders(cfg.graders_dir)
    meta = json.loads((run_dir / "meta.json").read_text())
    out = {"schema": "repoevals.grade/v1", "task": meta.get("task"), "family": None, "grader": None,
           "arm": meta.get("arm"), "agent": meta.get("agent"), "model": meta.get("model"),
           "label": meta.get("label"), "repeat": meta.get("repeat"), "status": "error", "pass": None,
           "metrics": {}, "details": {}, "process": {}, "integrity": {}, "errors": [],
           "agent_status": meta.get("agent_status"), "agent_seconds": meta.get("agent_seconds"),
           "agent_version": meta.get("agent_version"), "base_sha": meta.get("base_sha"),
           "task_hash": meta.get("task_hash"), "repoevals_version": __version__,
           "graded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if meta.get("agent_status") == "infra_error":  # the agent never really ran: not the agent's failure
        out["errors"].append(f"agent infrastructure error: {meta.get('agent_reason') or 'unknown'}")
        return _write(run_dir, out)
    try:
        ctx = context(run_dir, cfg, task)
        t = ctx.task
        out.update(family=t.family, grader=t.grader_type, created=str(t.metadata.get("created", "")))
        ok, why = host_supports(t)
        if not ok:
            out.update(status="skipped", errors=[f"skipped: {why}"])
            return _write(run_dir, out)
        if t.grader_type not in GRADERS:
            raise KeyError(f"unknown grader {t.grader_type!r}; known: {sorted(GRADERS)}")
        out["process"] = process_metrics(ctx.events, cfg.guidance_globs, t.grader.get("build_regex", ""))
        markers = [str(t.dir), ".agent-evals", "oracle.patch", "oracle_tests", "/hidden/", "mutants/"]
        tree = ctx.tree()
        ch = tree.changed(ctx.start_sha)
        roots = sorted({str(Path(x).resolve()) for x in (meta.get("source"), str(cfg.root)) if x})
        out["integrity"] = integrity_check(tree, ctx.start_sha, ctx.events, ch, cfg.test_globs,
                                           cfg.protected_globs + list(t.grader.get("protected", [])), markers,
                                           allow_history=bool(t.grader.get("allow_history", False)),
                                           forbidden_roots=roots)
        out["process"]["trace"] = "events" if ctx.events else ("none" if meta.get("agent") not in (None, "noop")
                                                                else "n/a")
        (run_dir / "agent.diff").write_text(tree.diff_text(ctx.start_sha))
        g = GRADERS[t.grader_type](ctx)
        for ev in g.get("meta_testing_suspect") or []:
            out["integrity"]["integrity_findings"].append({"check": "meta_testing", "violation": True,
                                                           "evidence": ev})
            out["integrity"]["integrity_ok"] = False
        out["metrics"], out["details"] = split_result(g)
        for k in t.gate:
            if isinstance(k, str) and k.isidentifier():
                out["metrics"].setdefault(k, None)
        passed = g.get("pass")
        gate_integrity = cfg.integrity_gate or bool(t.grader.get("integrity_gate", False))
        if gate_integrity and passed and not out["integrity"]["integrity_ok"]:
            passed = False
            out["errors"].append("integrity violation (gated)")
        out["pass"] = passed
        out["status"] = "pass" if passed else ("fail" if passed is False else "scored")
        if ctx.log:
            out["details"]["grader_log"] = ctx.log
    except Exception as e:  # not graded
        out.update(status="error", **{"pass": None})
        out["errors"].append(f"{type(e).__name__}: {e}")
        out["errors"].append(traceback.format_exc(limit=6))
    return _write(run_dir, out)


def _write(run_dir: Path, out: dict) -> dict:
    tmp = run_dir / "grade.json.tmp"
    tmp.write_text(json.dumps(out, indent=1, default=str))
    tmp.replace(run_dir / "grade.json")  # atomic: a crash never leaves a half-written grade
    return out


def apply_oracle(run_dir: Path, cfg: RepoConfig) -> str | None:
    load_repo_graders(cfg.graders_dir)
    ctx = context(run_dir, cfg)
    fn = ORACLES.get(ctx.task.grader_type)
    if fn is None:
        raise KeyError(f"grader {ctx.task.grader_type!r} has no oracle")
    text = fn(ctx)
    if text is not None:
        (run_dir / "answer.txt").write_text(text)
    return text
