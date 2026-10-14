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
from .util import apply_patch, git, git_commit, glob_match, resolve_rev, run

ARMS_HELP = """arms: guidance:raw (as checked in, default) | guidance:none (all guidance files deleted) |
guidance:file=<path> (only <path>, installed as AGENTS.md and CLAUDE.md) | <name> defined in [arms.<name>]"""
AGENT_FILES = ("AGENTS.md", "CLAUDE.md")  # Codex and most agents read AGENTS.md; Claude Code reads CLAUDE.md
GUIDANCE_NAMES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".cursorrules")


def _extract(source: Path, rev: str, dest: Path) -> None:
    """Materialize the tree of `rev` into dest. Uses read-tree + checkout-index with a private index rather than
    `git archive`, which would honour export-ignore/export-subst attributes and silently drop or rewrite files."""
    with tempfile.TemporaryDirectory() as td:
        env = {"GIT_INDEX_FILE": str(Path(td) / "index")}
        for args in (["read-tree", rev], ["checkout-index", "-a", "-f", f"--prefix={dest.resolve()}/"]):
            p = run(["git", "-C", str(source), *args], env=env)
            if p.rc != 0:
                raise RuntimeError(f"git {args[0]} {rev} failed in {source}: {p.out[:500]}")


def _guidance_files(ws: Path, globs: list[str]) -> list[str]:
    return sorted(p.relative_to(ws).as_posix() for p in ws.rglob("*") if p.is_file()
                  and ".git/" not in p.relative_to(ws).as_posix() + "/"
                  and glob_match(p.relative_to(ws).as_posix(), globs))


def apply_arm(ws: Path, cfg: RepoConfig, arm: str) -> list[str]:
    """Mutate the guidance files according to the arm; returns the files removed or installed.

    Built-in arms: guidance:raw, guidance:none, guidance:file=PATH. Named arms come from repoevals.toml:

        [arms.terse]
        remove = ["**/AGENTS.md"]              # default: every [guidance].globs file
        install = { "AGENTS.md" = "experiments/terse.md", "CLAUDE.md" = "experiments/terse.md" }
        overlay = "experiments/guidance-v2"    # directory copied onto the workspace (nested files, skills)
    """
    if arm in ("", "guidance:raw", "raw"):
        return []
    if arm in ("guidance:none", "none"):
        spec: dict = {}
    elif arm.startswith("guidance:file="):
        src = str(Path(arm.split("=", 1)[1]).expanduser().resolve())
        spec = {"install": {name: src for name in AGENT_FILES}}
    elif arm in cfg.arms:
        spec = dict(cfg.arms[arm])
    else:
        raise ValueError(f"unknown arm {arm!r}. {ARMS_HELP}")
    changed = _guidance_files(ws, list(spec.get("remove", cfg.guidance_globs)))
    for f in changed:
        (ws / f).unlink()
    if spec.get("overlay"):
        from .util import copytree_overlay
        src_dir = Path(spec["overlay"])
        src_dir = src_dir if src_dir.is_absolute() else cfg.root / src_dir
        if not src_dir.is_dir():
            raise FileNotFoundError(f"arm {arm!r}: overlay directory {src_dir} not found")
        changed += copytree_overlay(src_dir, ws)
    for dest, src in dict(spec.get("install", {})).items():
        src_path = Path(src) if Path(src).is_absolute() else cfg.root / src
        if not src_path.is_file():
            raise FileNotFoundError(f"arm {arm!r}: {src_path} not found")
        (ws / dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, ws / dest)
        changed.append(dest)
    return sorted(set(changed))


def _materialize_submodules(source: Path, base_sha: str, ws: Path) -> list[str]:
    """Write each submodule's tree at the recorded commit, from the source checkout's initialized submodules."""
    done = []
    for line in git(source, "ls-tree", "-r", base_sha).out.splitlines():
        meta_, _, path = line.partition("\t")
        mode, kind, sha = meta_.split()
        if mode != "160000":
            continue
        sub = source / path
        if git(sub, "cat-file", "-e", f"{sha}^{{commit}}").rc != 0:
            raise RuntimeError(f"submodule {path} at {sha[:12]} is not available; run "
                               f"`git submodule update --init --recursive` in {source}")
        target = ws / path
        target.mkdir(parents=True, exist_ok=True)
        _extract(sub, sha, target)
        done.append(path)
    return done


def ancestor_guidance(ws: Path) -> list[str]:
    """Guidance files in directories above the workspace and in the user's agent config, which agents such as
    Claude Code load automatically and which therefore apply to every arm."""
    found = []
    for d in ws.resolve().parents:
        for name in GUIDANCE_NAMES:
            if (d / name).is_file():
                found.append(str(d / name))
    home = Path.home()
    for p in (home / ".claude" / "CLAUDE.md", home / ".codex" / "AGENTS.md", home / ".gemini" / "GEMINI.md"):
        if p.is_file():
            found.append(str(p))
    return sorted(set(found))


def _hash_tree(d: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        h.update(f.relative_to(d).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def prepare(cfg: RepoConfig, task: Task, run_dir: Path, source: Path, arm: str = "guidance:raw") -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    ws = run_dir / "workspace"
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir()
    base_sha = resolve_rev(source, task.base, cfg.upstream)
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
    submodules = _materialize_submodules(source, base_sha, ws) if cfg.environment.get("submodules") else []
    # make ws its own repository before applying patches: `git apply` run inside a subdirectory of another
    # repository (e.g. runs/ under the source checkout) silently skips paths outside that subdirectory
    git(ws, "init", "-q", "-b", "main", check=True)
    if task.has("setup.patch"):
        ap = apply_patch(ws, task.file("setup.patch"))
        if not ap.ok:
            raise RuntimeError(f"{task.id}: setup.patch does not apply at {base_sha[:12]}: {ap.out[:1000]}")
    arm_files = apply_arm(ws, cfg, arm)
    import hashlib
    guidance = {f: hashlib.sha256((ws / f).read_bytes()).hexdigest()[:12]
                for f in _guidance_files(ws, cfg.guidance_globs)}
    setup = str(cfg.environment.get("setup", "")).strip()
    if setup:  # e.g. a native build, so the agent starts from a built tree; ignored artifacts stay untracked
        from .util import run_shell
        sp = run_shell(setup, cwd=ws, timeout=float(cfg.environment.get("setup_timeout_sec", 7200)),
                       env=cfg.test.env)
        (run_dir / "setup.log").write_text(sp.out[-200000:])
        if not sp.ok:
            raise RuntimeError(f"{task.id}: [environment].setup failed (rc={sp.rc}); see {run_dir / 'setup.log'}")
    start = git_commit(ws, "workspace")
    if task.has(task.grader.get("review_patch", "review.patch")):
        ap = apply_patch(ws, task.file(task.grader.get("review_patch", "review.patch")))
        if not ap.ok:
            raise RuntimeError(f"{task.id}: review.patch does not apply: {ap.out[:1000]}")
        start = git_commit(ws, task.grader.get("review_title", "Change under review"))
    git(ws, "reflog", "expire", "--expire=now", "--all")
    problems = leak_scan(ws)
    if problems:
        raise RuntimeError(f"{task.id}: workspace leaks: {problems}")
    # pristine copy of the workspace history, outside the workspace: graders diff against it (util.WorkTree)
    hd = run_dir / ".repoevals"
    if hd.exists():
        shutil.rmtree(hd)
    hd.mkdir()
    git(run_dir, "clone", "-q", "--bare", str(ws.resolve()), str((hd / "start.git").resolve()), check=True)
    shutil.copytree(task.dir, hd / "tasks" / task.id, symlinks=False)  # grading uses this copy (reproducible regrades)
    (run_dir / "instruction.md").write_text(task.instruction)
    (run_dir / "task.json").write_text(json.dumps(task.to_json(), indent=1))
    from . import __version__
    meta = {"task": task.id, "task_dir": str(task.dir), "task_hash": _hash_tree(task.dir), "base": task.base,
            "base_sha": base_sha, "start_sha": start, "arm": arm, "arm_files": arm_files, "guidance": guidance,
            "ancestor_guidance": ancestor_guidance(ws), "submodules": submodules,
            "eval_files_removed": len(removed), "source": str(source.resolve()),
            "source_sha": git(source, "rev-parse", "HEAD").out.strip(), "repoevals_version": __version__,
            "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "repo": cfg.id}
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def update_meta(run_dir: Path, **kw) -> dict:
    p = run_dir / "meta.json"
    meta = json.loads(p.read_text()) if p.exists() else {}
    meta.update(kw)
    p.write_text(json.dumps(meta, indent=1))
    return meta
