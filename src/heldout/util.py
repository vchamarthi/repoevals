"""Small shared helpers: subprocess, git, globbing, JSON extraction. Standard library only."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Sequence

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class Proc:
    rc: int
    out: str
    timed_out: bool = False
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out


def run(cmd: str | Sequence[str], cwd: Path | str | None = None, timeout: float | None = None,
        env: Mapping[str, str] | None = None, stdin: str | None = None) -> Proc:
    """Run a command, merging stderr into stdout. A string is split with shlex (no shell)."""
    import time

    argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    full_env = {**os.environ, **(env or {})}
    t0 = time.monotonic()
    try:
        p = subprocess.run(argv, cwd=cwd, env=full_env, input=stdin, text=True, capture_output=True,
                           timeout=timeout)
        return Proc(p.returncode, (p.stdout or "") + (p.stderr or ""), seconds=time.monotonic() - t0)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return Proc(-1, out, timed_out=True, seconds=time.monotonic() - t0)
    except FileNotFoundError as e:
        return Proc(127, str(e), seconds=time.monotonic() - t0)


def run_shell(cmd: str, cwd: Path | str | None = None, timeout: float | None = None,
              env: Mapping[str, str] | None = None) -> Proc:
    """Run a repo-configured command line through bash (config commands may use pipes, &&, env vars)."""
    return run(["bash", "-c", cmd], cwd=cwd, timeout=timeout, env=env)


def git(repo: Path | str, *args: str, check: bool = False, stdin: str | None = None) -> Proc:
    p = run(["git", "-c", "core.quotepath=off", "-c", "advice.detachedHead=false", *args], cwd=repo, stdin=stdin)
    if check and p.rc != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {p.out.strip()[:2000]}")
    return p


def git_init_snapshot(ws: Path, message: str) -> str:
    """Commit everything in ws as a single root commit; return its sha."""
    git(ws, "init", "-q", "-b", "main", check=True)
    git(ws, "add", "-A", "-f", check=True)
    env_args = ["-c", "user.name=heldout", "-c", "user.email=heldout@localhost", "-c", "commit.gpgsign=false"]
    git(ws, *env_args, "commit", "-q", "--no-verify", "--allow-empty", "-m", message, check=True)
    return git(ws, "rev-parse", "HEAD", check=True).out.strip()


def git_commit(ws: Path, message: str) -> str:
    env_args = ["-c", "user.name=heldout", "-c", "user.email=heldout@localhost", "-c", "commit.gpgsign=false"]
    git(ws, "add", "-A", "-f", check=True)
    git(ws, *env_args, "commit", "-q", "--no-verify", "--allow-empty", "-m", message, check=True)
    return git(ws, "rev-parse", "HEAD", check=True).out.strip()


def apply_patch(ws: Path, patch: Path, reverse: bool = False, check_only: bool = False) -> Proc:
    args = ["apply", "--whitespace=nowarn"]
    if reverse:
        args.append("-R")
    if check_only:
        args.append("--check")
    return git(ws, *args, str(patch.resolve()))


def changed_files(ws: Path, base: str) -> dict[str, str]:
    """Files that differ between `base` and the working tree (tracked or not): {path: status letter}."""
    git(ws, "add", "-A", "-N")  # intent-to-add so untracked files show up in diff
    out = git(ws, "diff", "--name-status", "--no-renames", base).out
    res: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            res[parts[-1]] = parts[0][0]
    return res


def diff_text(ws: Path, base: str, paths: Iterable[str] | None = None) -> str:
    git(ws, "add", "-A", "-N")
    args = ["diff", "--no-renames", "--no-color", base]
    if paths is not None:
        args += ["--", *paths]
    return git(ws, *args).out


def diff_stats(ws: Path, base: str) -> tuple[int, int]:
    """(lines added, lines removed) against base, new files included."""
    git(ws, "add", "-A", "-N")
    added = removed = 0
    for line in git(ws, "diff", "--numstat", "--no-renames", base).out.splitlines():
        a, r, *_ = line.split("\t") + ["", ""]
        if a.isdigit():
            added += int(a)
        if r.isdigit():
            removed += int(r)
    return added, removed


def restore_paths(ws: Path, base: str, paths: Iterable[str]) -> list[str]:
    """Reset each path to its content at base (deleting it if it did not exist there). Returns restored paths."""
    restored = []
    for p in paths:
        exists = git(ws, "cat-file", "-e", f"{base}:{p}").rc == 0
        if exists:
            git(ws, "checkout", base, "--", p, check=True)
        else:
            target = ws / p
            if target.is_file() or target.is_symlink():
                target.unlink()
        restored.append(p)
    return restored


@lru_cache(maxsize=512)
def _glob_re(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-like glob (`**`, `*`, `?`) to a regex matched against posix relative paths."""
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(path: str, patterns: Iterable[str]) -> bool:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return any(_glob_re(p).match(path) for p in patterns)


def last_json(text: str):
    """The last fenced ```json block in text, else the last top-level JSON array/object, else None."""
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text or "", re.S)
    for b in reversed(blocks):
        try:
            return json.loads(b)
        except ValueError:
            continue
    for opener, closer in (("[", "]"), ("{", "}")):
        end = (text or "").rfind(closer)
        start = (text or "").rfind(opener, 0, end)
        while start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                start = text.rfind(opener, 0, start)
    return None


def copytree_overlay(src: Path, dst: Path) -> list[str]:
    """Copy every file under src onto dst (overwriting); returns the relative paths written."""
    written = []
    for f in sorted(src.rglob("*")):
        if f.is_file():
            rel = f.relative_to(src).as_posix()
            if rel.endswith(".oracle"):  # stored with a suffix so the repo's own tooling ignores it
                rel = rel[: -len(".oracle")]
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            written.append(rel)
    return written


def read_toml(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def fmt(template: str, **kw) -> str:
    """str.format that leaves unknown {placeholders} untouched."""
    class _D(dict):
        def __missing__(self, k):
            return "{" + k + "}"
    return template.format_map(_D(**{k: v for k, v in kw.items() if v is not None}))


def within(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
