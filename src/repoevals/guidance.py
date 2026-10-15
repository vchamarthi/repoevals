"""`repoevals guidance-lint`: do agent instructions (AGENTS.md, CLAUDE.md, Copilot instructions) match the repo?

Stale guidance is worse than none: an agent told to run a script that was deleted, or to edit a file that moved,
wastes its budget. This check extracts every `backticked` token that looks like a repository path and reports the
ones that do not exist. It is deterministic, takes seconds, and is meant for CI on every PR (report-only).
"""
from __future__ import annotations

import re
from pathlib import Path

from .util import glob_match, run

PATHLIKE = re.compile(r"`([^`\s]+)`")
EXT = re.compile(r"\.(py|pyx|pxd|pxi|c|cc|cpp|cxx|h|hpp|cu|toml|cfg|ini|ya?ml|json|md|rst|txt|sh|cmake|bzl|in|src)$")


EVAL_DIRS = [".agent-evals/**", "**/node_modules/**", "**/.venv/**", "**/venv/**", "runs/**"]


def guidance_files(root: Path, globs: list[str], exclude: list[str] | None = None) -> list[Path]:
    """The repository's guidance files. Evaluation artifacts (tasks, drafts, run output) are never guidance."""
    skip = EVAL_DIRS + list(exclude or [])
    return sorted(p for p in root.rglob("*") if p.is_file() and ".git/" not in p.relative_to(root).as_posix() + "/"
                  and glob_match(p.relative_to(root).as_posix(), globs)
                  and not glob_match(p.relative_to(root).as_posix(), skip))


def claims(text: str) -> list[str]:
    out = []
    for tok in PATHLIKE.findall(text):
        tok = tok.strip().rstrip(".,:;)")
        if tok.startswith(("http", "-", "$", "<")) or "*" in tok or "{" in tok:
            continue
        if re.fullmatch(r"\.[\w.]+", tok):  # a bare extension (`.c`, `.c.src`), not a path
            continue
        if "/" in tok or EXT.search(tok):
            out.append(tok.split("::")[0].split(":")[0])
    return out


def _ignored(root: Path, claim: str) -> bool:
    """Whether git ignores the path: a generated artifact the guidance may legitimately describe."""
    return run(["git", "check-ignore", "-q", "--", claim], cwd=root).rc == 0


def lint(root: Path, globs: list[str], exclude: list[str] | None = None) -> list[dict]:
    results = []
    for f in guidance_files(root, globs, exclude):
        text = f.read_text(errors="replace")
        for c in sorted(set(claims(text))):
            cand = [root / c.lstrip("/"), f.parent / c]
            ok = any(p.exists() for p in cand) or any(root.glob(f"**/{Path(c).name}")) and "/" not in c
            rel = (f.parent / c).relative_to(root).as_posix() + ("/" if c.endswith("/") else "")
            ok = ok or _ignored(root, c) or _ignored(root, rel)
            results.append({"file": f.relative_to(root).as_posix(), "claim": c, "exists": bool(ok)})
    return results
