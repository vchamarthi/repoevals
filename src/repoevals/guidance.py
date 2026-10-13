"""`repoevals guidance-lint`: do the agent instructions (AGENTS.md, CLAUDE.md, copilot instructions) still match the repo?

Stale guidance is worse than none: an agent told to run a script that was deleted, or to edit a file that moved,
wastes its budget. This check extracts every `backticked` token that looks like a repository path and reports the
ones that do not exist. It is deterministic, takes seconds, and is meant for CI on every PR (report-only).
"""
from __future__ import annotations

import re
from pathlib import Path

from .util import glob_match

PATHLIKE = re.compile(r"`([^`\s]+)`")
EXT = re.compile(r"\.(py|pyx|pxd|pxi|c|cc|cpp|cxx|h|hpp|cu|toml|cfg|ini|ya?ml|json|md|rst|txt|sh|cmake|bzl|in|src)$")


def guidance_files(root: Path, globs: list[str]) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and ".git/" not in p.relative_to(root).as_posix() + "/"
                  and glob_match(p.relative_to(root).as_posix(), globs))


def claims(text: str) -> list[str]:
    out = []
    for tok in PATHLIKE.findall(text):
        tok = tok.strip().rstrip(".,:;)")
        if tok.startswith(("http", "-", "$", "<")) or "*" in tok or "{" in tok:
            continue
        if "/" in tok or EXT.search(tok):
            out.append(tok.split("::")[0].split(":")[0])
    return out


def lint(root: Path, globs: list[str]) -> list[dict]:
    results = []
    for f in guidance_files(root, globs):
        text = f.read_text(errors="replace")
        for c in sorted(set(claims(text))):
            cand = [root / c.lstrip("/"), f.parent / c]
            ok = any(p.exists() for p in cand) or any(root.glob(f"**/{Path(c).name}")) and "/" not in c
            results.append({"file": f.relative_to(root).as_posix(), "claim": c, "exists": bool(ok)})
    return results
