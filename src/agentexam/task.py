"""Task definitions: one directory per task containing `task.toml` and `instruction.md`.

The layout is a superset of the Harbor / Terminal-Bench task directory (task.toml + instruction.md), so tasks can be
exported to Harbor with `agentexam export harbor`. Everything agentexam-specific lives under [grader] and [metadata].

    tasks/fix_kmeans_empty_reloc/
      task.toml            required
      instruction.md       required (or `prompt = "..."` in task.toml)
      setup.patch          optional: applied before the agent starts (e.g. re-introduces a bug)
      oracle.patch         optional: reference solution (else: reverse of setup.patch)
      hidden/              optional: files overlaid onto the agent's tree only while grading
      review.patch         review tasks: the change under review (committed on top of the base)
      manifest.json        seeded-review tasks: seeds, decoys
      gold.json            replay-review tasks: human review threads
      oracle_answer.json   review tasks: reference findings
      mutants/*.patch      test-writing tasks: faults the agent's tests must detect
      oracle_tests/        test-writing tasks: reference tests
      probe.py             numeric tasks: cases + candidate/reference callables

    # task.toml
    version = "1.0"
    [metadata]
    family = "fix"                      # build | fix | feature | test | review | numeric | perf | custom
    base = "a492e703^"                  # commit of the upstream repo the workspace is snapshotted from
    created = 2026-05-14                # contamination control: date the fix/content became public
    source = "https://github.com/org/repo/pull/3742"
    difficulty = "medium"
    tags = ["kmeans", "cpu"]
    [environment]
    requires = { cpus = 4, cpu_flags = ["avx2"], devices = ["cpu"] }
    [agent]
    timeout_sec = 3600
    [verifier]
    timeout_sec = 1800
    [grader]
    type = "hidden_tests"
    targets = ["tests/test_kmeans.py"]
    regression_targets = ["tests/test_kmeans_badarg.py"]
    gate = ["strict_pass"]
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .util import read_toml

FAMILIES = {"build", "fix", "feature", "test", "review", "numeric", "perf", "port", "docs", "custom"}
CANARY_PREFIX = "AGENTEXAM CANARY GUID"


@dataclass
class Task:
    id: str
    dir: Path
    family: str
    base: str
    instruction: str
    grader: dict
    metadata: dict = field(default_factory=dict)
    environment: dict = field(default_factory=dict)
    agent: dict = field(default_factory=dict)
    verifier: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def grader_type(self) -> str:
        return self.grader.get("type", "")

    @property
    def gate(self) -> list:
        return list(self.grader.get("gate", []))

    def file(self, name: str) -> Path:
        return self.dir / name

    def has(self, name: str) -> bool:
        return (self.dir / name).exists()

    @property
    def requires(self) -> dict:
        return self.environment.get("requires", {})

    def to_json(self) -> dict:
        return {"id": self.id, "family": self.family, "base": self.base, "grader": self.grader,
                "metadata": self.metadata, "environment": self.environment, "dir": str(self.dir)}


def load_task(task_dir: Path) -> Task:
    task_dir = task_dir.resolve()
    if (task_dir / "task.toml").exists():
        data = read_toml(task_dir / "task.toml")
    elif (task_dir / "task.json").exists():  # legacy flat format (oneDAL dev/agent_evals): best-effort mapping
        legacy = json.loads((task_dir / "task.json").read_text())
        data = {"metadata": {k: v for k, v in legacy.items() if k in ("family", "base", "source_pr", "id")},
                "grader": {"type": legacy.get("grader", ""), **{k: v for k, v in legacy.items()
                           if k not in ("id", "family", "base", "grader", "prompt")}},
                "prompt": legacy.get("prompt", "")}
    else:
        raise FileNotFoundError(f"{task_dir}: no task.toml")
    md = dict(data.get("metadata", {}))
    instr_path = task_dir / "instruction.md"
    instruction = instr_path.read_text() if instr_path.exists() else data.get("prompt", "")
    return Task(
        id=md.get("id", task_dir.name),
        dir=task_dir,
        family=md.get("family", "custom"),
        base=str(md.get("base", "HEAD")),
        instruction=instruction,
        grader=dict(data.get("grader", {})),
        metadata=md,
        environment=dict(data.get("environment", {})),
        agent=dict(data.get("agent", {})),
        verifier=dict(data.get("verifier", {})),
        raw=data,
    )


def discover(tasks_dir: Path, select: list[str] | None = None) -> list[Task]:
    """All tasks under tasks_dir (sorted). `select` filters by id, id prefix, family:<f> or tag:<t>."""
    if not tasks_dir.is_dir():
        return []
    tasks = [load_task(d) for d in sorted(tasks_dir.iterdir())
             if d.is_dir() and ((d / "task.toml").exists() or (d / "task.json").exists())]
    if not select:
        return tasks

    def keep(t: Task) -> bool:
        for s in select:
            if s.startswith("family:") and t.family == s[7:]:
                return True
            if s.startswith("tag:") and s[4:] in t.metadata.get("tags", []):
                return True
            if t.id == s or (s.endswith("*") and t.id.startswith(s[:-1])):
                return True
        return False
    return [t for t in tasks if keep(t)]


def host_supports(task: Task) -> tuple[bool, str]:
    """Whether this machine meets [environment].requires; the reason when it does not (task is SKIPPED)."""
    req = task.requires
    if not req:
        return True, ""
    if req.get("cpus") and (os.cpu_count() or 1) < int(req["cpus"]):
        return False, f"needs {req['cpus']} CPUs, host has {os.cpu_count()}"
    flags = set(req.get("cpu_flags", []))
    vendors = set(req.get("cpu_vendors", []))
    if flags or vendors:
        info = Path("/proc/cpuinfo").read_text() if Path("/proc/cpuinfo").exists() else ""
        have = set()
        vendor = ""
        for line in info.splitlines():
            if line.startswith("flags"):
                have |= set(line.split(":", 1)[1].split())
            if line.startswith("vendor_id"):
                vendor = line.split(":", 1)[1].strip()
        if flags - have:
            return False, f"missing CPU flags {sorted(flags - have)}"
        if vendors and vendor not in vendors:
            return False, f"CPU vendor {vendor or 'unknown'} not in {sorted(vendors)}"
    devices = set(req.get("devices", [])) - {"cpu"}
    available = set(filter(None, os.environ.get("AGENTEXAM_DEVICES", "cpu").split(",")))
    if devices - available:
        return False, f"needs devices {sorted(devices - available)} (set AGENTEXAM_DEVICES)"
    return True, ""
