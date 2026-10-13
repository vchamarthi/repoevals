"""Prepare a history-free workspace for one task run.

    run_dir/
      workspace/     snapshot of the source repo at task.base, eval files removed, setup.patch applied,
                     arm applied, committed as a single root commit (review tasks: + one commit with the change)
      task.json      resolved task (what the run was prepared from)
      meta.json      start_sha, base, arm, timestamps; later agent/model/usage
      instruction.md the prompt the agent receives

The agent never sees: git history (so it cannot mine the upstream fix), the task directory, hidden tests, oracles,
mutants, or this framework's config. `leak_scan` verifies this after preparation.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

from .config import RepoConfig
from .integrity import leak_scan
from .task import Task
from .util import apply_patch, git, git_commit, glob_match, run

ARMS_HELP = """arms: guidance:raw (as checked in, default) | guidance:none (all guidance files deleted) |
guidance:file=<path> (replace the root AGENTS.md with <path>, delete the rest)"""


def _extract(source: Path, rev: str, dest: Path) -> None:
    """Materialize the tree of `rev` into dest. Uses read-tree + checkout-index with a private index rather than
    `git archive`, which would honour export-ignore/export-subst attributes and silently drop or rewrite files."""
    with tempfile.TemporaryDirectory() as td:
        env = {"GIT_INDEX_FILE": str(Path(td) / "index")}
        for args in (["read-tree", rev], ["checkout-index", "-a", "-f", f"--prefix={dest.resolve()}/"]):
            p = run(["git", "-C", str(source), *args], env=env)
            if p.rc != 0:
                raise RuntimeError(f"git {args[0]} {rev} failed in {source}: {p.out[:500]}")


def apply_arm(ws: Path, cfg: RepoConfig, arm: str) -> list[str]:
    """Mutate the guidance files according to the arm; returns the files removed/replaced."""
    if arm in ("", "guidance:raw", "raw"):
        return []
    files = [p.relative_to(ws).as_posix() for p in ws.rglob("*") if p.is_file()
             and ".git/" not in p.relative_to(ws).as_posix() + "/"
             and glob_match(p.relative_to(ws).as_posix(), cfg.guidance_globs)]
    for f in files:
        (ws / f).unlink()
    if arm in ("guidance:none", "none"):
        return files
    if arm.startswith("guidance:file="):
        src = Path(arm.split("=", 1)[1]).expanduser()
        shutil.copy2(src, ws / "AGENTS.md")
        return files + ["AGENTS.md"]
    raise ValueError(f"unknown arm {arm!r}. {ARMS_HELP}")


def prepare(cfg: RepoConfig, task: Task, run_dir: Path, source: Path, arm: str = "guidance:raw") -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    ws = run_dir / "workspace"
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir()
    base_sha = git(source, "rev-parse", f"{task.base}^{{commit}}", check=True).out.strip()
    _extract(source, base_sha, ws)
    removed = []
    for p in sorted(ws.rglob("*"), reverse=True):
        rel = p.relative_to(ws).as_posix()
        if p.is_file() and glob_match(rel, cfg.eval_globs):
            p.unlink()
            removed.append(rel)
    for d in sorted((p for p in ws.rglob("*") if p.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    # make ws its own repository before applying patches: `git apply` run inside a subdirectory of another
    # repository (e.g. runs/ under the source checkout) silently skips paths outside that subdirectory
    git(ws, "init", "-q", "-b", "main", check=True)
    if task.has("setup.patch"):
        ap = apply_patch(ws, task.file("setup.patch"))
        if not ap.ok:
            raise RuntimeError(f"{task.id}: setup.patch does not apply at {base_sha[:12]}: {ap.out[:1000]}")
    arm_files = apply_arm(ws, cfg, arm)
    start = git_commit(ws, "workspace")
    if task.has(task.grader.get("review_patch", "review.patch")):
        ap = apply_patch(ws, task.file(task.grader.get("review_patch", "review.patch")))
        if not ap.ok:
            raise RuntimeError(f"{task.id}: review.patch does not apply: {ap.out[:1000]}")
        start = git_commit(ws, task.grader.get("review_title", "Change under review"))
    git(ws, "reflog", "expire", "--expire=now", "--all")
    git(ws, "gc", "-q", "--prune=now")
    problems = leak_scan(ws)
    if problems:
        raise RuntimeError(f"{task.id}: workspace leaks: {problems}")
    # pristine copy of the workspace history, outside the workspace: graders diff against it (util.WorkTree)
    hd = run_dir / ".repoevals"
    if hd.exists():
        shutil.rmtree(hd)
    hd.mkdir()
    git(run_dir, "clone", "-q", "--bare", str(ws.resolve()), str((hd / "start.git").resolve()), check=True)
    (run_dir / "instruction.md").write_text(task.instruction)
    (run_dir / "task.json").write_text(json.dumps(task.to_json(), indent=1))
    meta = {"task": task.id, "task_dir": str(task.dir), "base": task.base, "base_sha": base_sha, "start_sha": start,
            "arm": arm, "arm_files": arm_files, "eval_files_removed": len(removed),
            "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "repo": cfg.id}
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def update_meta(run_dir: Path, **kw) -> dict:
    p = run_dir / "meta.json"
    meta = json.loads(p.read_text()) if p.exists() else {}
    meta.update(kw)
    p.write_text(json.dumps(meta, indent=1))
    return meta
