"""Export repoevals tasks to the Harbor task format (harborframework.com; also used by Terminal-Bench 2+), so the same
tasks can be run by Harbor's agent adapters and cloud sandboxes.

    <out>/<task_id>/
      task.toml                 Harbor fields + [metadata] copied; repoevals's [grader] kept under [metadata.repoevals]
      instruction.md
      environment/Dockerfile    from [environment].dockerfile in repoevals.toml, or a minimal template
      solution/solve.sh         applies the reference solution
      tests/test.sh             runs `repoevals grade` inside the container, writes /logs/verifier/reward.txt
      tests/task/               the full repoevals task directory (hidden tests, mutants, manifests)

Harbor uploads tests/ only at verification time, which matches repoevals's rule that the agent never sees hidden
material. The exported environment must contain the repository at /workspace with git and repoevals installed.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import RepoConfig
from .task import Task

DOCKERFILE = """FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir repoevals
# The workspace (repository snapshot at the task base, prepared by `repoevals prepare`) is copied here:
COPY workspace/ /workspace/
WORKDIR /workspace
"""

TEST_SH = """#!/usr/bin/env bash
set -uo pipefail
mkdir -p /logs/verifier
RUN=/tmp/repoevals-run
mkdir -p "$RUN"
ln -sfn /workspace "$RUN/workspace"  # graded in place; repoevals resolves the link
cp /tests/meta.json "$RUN/meta.json"
sed -i "s#__TASK_DIR__#/tests/task#" "$RUN/meta.json"
[ -f /logs/agent/answer.txt ] && cp /logs/agent/answer.txt "$RUN/answer.txt"
repoevals --config /tests/repoevals.toml grade "$RUN" || true
python3 - <<'PY'
import json
g = json.load(open("/tmp/repoevals-run/grade.json"))
open("/logs/verifier/reward.txt", "w").write("1" if g.get("pass") else "0")
json.dump({"pass": g.get("pass"), **{k: v for k, v in g.get("metrics", {}).items() if isinstance(v, (int, float))}},
          open("/logs/verifier/reward.json", "w"))
PY
"""


def _toml_value(v) -> str:
    import json
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(x)}" for k, x in v.items()) + " }"
    return json.dumps(str(v))


def export_task(t: Task, cfg: RepoConfig, out: Path, workspace_meta: dict | None = None) -> Path:
    d = out / t.id
    if d.exists():
        shutil.rmtree(d)
    (d / "environment").mkdir(parents=True)
    (d / "solution").mkdir()
    (d / "tests").mkdir()
    md = {k: v for k, v in t.metadata.items() if not isinstance(v, dict)}
    lines = ['version = "1.0"', "", "[metadata]"]
    lines += [f"{k} = {_toml_value(v)}" for k, v in md.items()]
    lines += ["", "[metadata.repoevals]"] + [f"{k} = {_toml_value(v)}" for k, v in t.grader.items()]
    lines += ["", "[verifier]", f"timeout_sec = {float(t.verifier.get('timeout_sec', 1800))}",
              "", "[agent]", f"timeout_sec = {float(t.agent.get('timeout_sec', 3600))}",
              "", "[environment]", f"build_timeout_sec = {float(t.environment.get('build_timeout_sec', 1800))}"]
    if t.environment.get("cpus"):
        lines.append(f"cpus = {int(t.environment['cpus'])}")
    (d / "task.toml").write_text("\n".join(lines) + "\n")
    (d / "instruction.md").write_text(t.instruction)
    dockerfile = cfg.environment.get("dockerfile")
    if dockerfile and (cfg.root / dockerfile).exists():
        shutil.copy2(cfg.root / dockerfile, d / "environment" / "Dockerfile")
    else:
        (d / "environment" / "Dockerfile").write_text(DOCKERFILE)
    solve = ["#!/usr/bin/env bash", "set -euo pipefail", "cd /workspace"]
    if t.has("oracle.patch"):
        shutil.copy2(t.file("oracle.patch"), d / "solution" / "oracle.patch")
        solve.append("git apply /solution/oracle.patch")
    elif t.has("setup.patch"):
        shutil.copy2(t.file("setup.patch"), d / "solution" / "setup.patch")
        solve.append("git apply -R /solution/setup.patch")
    if t.has("oracle_answer.json"):
        shutil.copy2(t.file("oracle_answer.json"), d / "solution" / "oracle_answer.json")
        solve += ["mkdir -p /logs/agent", "{ echo '```json'; cat /solution/oracle_answer.json; echo '```'; } "
                  "> /logs/agent/answer.txt"]
    (d / "solution" / "solve.sh").write_text("\n".join(solve) + "\n")
    shutil.copytree(t.dir, d / "tests" / "task")
    cfg_file = next((cfg.root / n for n in ("repoevals.toml", ".agent-evals/repoevals.toml") if (cfg.root / n).exists()),
                    None)
    if cfg_file:
        shutil.copy2(cfg_file, d / "tests" / "repoevals.toml")
    import json
    meta = dict(workspace_meta or {})
    meta.update(task=t.id, task_dir="__TASK_DIR__", start_sha=meta.get("start_sha", "HEAD"))
    (d / "tests" / "meta.json").write_text(json.dumps(meta, indent=1))
    (d / "tests" / "test.sh").write_text(TEST_SH)
    for f in (d / "tests" / "test.sh", d / "solution" / "solve.sh"):
        f.chmod(0o755)
    return d
