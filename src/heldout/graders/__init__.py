"""Grader registry.

A grader is a function `grade(ctx: GradeContext) -> dict`. The dict holds metrics (numbers/bools), details
(strings/lists/objects) and "pass": True / False / None (None = score-only, or not measurable here).
An oracle is `oracle(ctx: GradeContext) -> str | None`: it puts the reference solution into ctx.workspace and/or
returns the reference final answer; `heldout check` uses it to prove every task is solvable and every grader
accepts a correct solution.

Graders come from three places, later ones overriding earlier ones:
  1. built-in modules in this package (each exports GRADERS and optionally ORACLES dicts)
  2. installed packages exposing entry points in the "heldout.graders" group (value: module with GRADERS/ORACLES)
  3. repo-local *.py files in the directory named by `[repo].graders` in heldout.toml
"""
from __future__ import annotations

import importlib
import importlib.util
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..adapters import Adapter, get_adapter
from ..config import RepoConfig
from ..task import Task

GRADERS: dict[str, Callable] = {}
ORACLES: dict[str, Callable] = {}


@dataclass
class GradeContext:
    run_dir: Path
    workspace: Path
    task: Task
    config: RepoConfig
    start_sha: str
    answer: str = ""
    events: list = field(default_factory=list)
    log: list = field(default_factory=list)

    @property
    def g(self) -> dict:
        """The task's [grader] table."""
        return self.task.grader

    def adapter(self) -> Adapter:
        return get_adapter(self.config.test, self.task.grader)

    def note(self, msg: str) -> None:
        self.log.append(msg)


def _register(mod) -> None:
    GRADERS.update(getattr(mod, "GRADERS", {}))
    ORACLES.update(getattr(mod, "ORACLES", {}))


def _load_builtin() -> None:
    for m in pkgutil.iter_modules(__path__):
        _register(importlib.import_module(f"{__name__}.{m.name}"))


def _load_entry_points() -> None:
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group="heldout.graders")
    except Exception:  # pragma: no cover
        return
    for ep in eps:
        try:
            _register(ep.load())
        except Exception as e:  # a broken plugin must not take the core down
            print(f"heldout: grader plugin {ep.name} failed to load: {e}")


def load_repo_graders(directory: Path | None) -> None:
    if not directory or not directory.is_dir():
        return
    for f in sorted(directory.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"heldout_repo_graders.{f.stem}", f)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _register(mod)


_load_builtin()
_load_entry_points()
