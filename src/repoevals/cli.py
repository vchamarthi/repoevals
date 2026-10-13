"""repoevals command line.

  repoevals init                         scaffold repoevals.toml + .agent-evals/tasks/ in the current repo
  repoevals list                         list tasks
  repoevals validate [--source DIR]      static checks of every task (CI gate, seconds)
  repoevals check    [--source DIR]      null/oracle/probe self-check of graders (CI, no LLM, no secrets)
  repoevals prepare TASK RUN_DIR         snapshot a workspace for one task (history-free)
  repoevals grade RUN_DIR                grade a run directory, write grade.json
  repoevals run --agent A [--arm ...] [--repeats N] [--model M] [--docker IMG] [TASK...]
                                       prepare + run agent + grade, for a matrix of tasks x arms x repeats
  repoevals report RUNS_DIR [--out DIR]  aggregate grade.json files (CIs, paired arm deltas, pass@k, cost)
  repoevals export harbor --out DIR      export tasks in Harbor format
  repoevals guidance-lint                check paths named in AGENTS.md & co. exist
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .config import load_config
from .task import discover, load_task

TASKS_HELP = "task ids, id prefixes ending in *, family:<name> or tag:<name> (default: all tasks)"
SOURCE_HELP = "repository with full history to snapshot task bases from (default: the config's repository)"

INIT_TOML = """# repoevals configuration — see https://github.com/vchamarthi/repoevals/blob/main/docs/configuration.md
[repo]
id = "{name}"
tasks = ".agent-evals/tasks"

[guidance]
globs = ["**/AGENTS.md", "**/CLAUDE.md", ".github/copilot-instructions.md", ".github/instructions/*.md"]

[policy]
test_globs = ["tests/**", "**/tests/**", "**/test_*.py"]
library_globs = ["{name}/**"]
protected_globs = []

[test]
adapter = "pytest"            # pytest | bazel | ctest | command
build = ""                    # e.g. "pip install -e . --no-build-isolation -q"
timeout_sec = 1800
"""

EXAMPLE_TASK = """version = "1.0"

[metadata]
family = "fix"
base = "HEAD"
created = "{today}"
source = ""
difficulty = "easy"
tags = ["example"]

[agent]
timeout_sec = 1800

[verifier]
timeout_sec = 900

[grader]
type = "hidden_tests"
targets = ["tests/test_example_hidden.py"]
gate = ["strict_pass"]
"""


def _cfg(args):
    return load_config(Path(args.config) if args.config else None)


def _tasks(cfg, select):
    return discover(cfg.tasks_dir, select or None)


def _source(args, cfg) -> Path:
    return Path(args.source).resolve() if getattr(args, "source", None) else cfg.root


def cmd_init(args) -> int:
    root = Path.cwd()
    if (root / "repoevals.toml").exists():
        print("repoevals.toml already exists")
        return 1
    (root / "repoevals.toml").write_text(INIT_TOML.replace("{name}", root.name))
    ex = root / ".agent-evals" / "tasks" / "example_fix"
    (ex / "hidden" / "tests").mkdir(parents=True, exist_ok=True)
    (ex / "task.toml").write_text(EXAMPLE_TASK.replace("{today}", time.strftime("%Y-%m-%d")))
    (ex / "instruction.md").write_text("Describe the bug as a user would report it. Do not hint at the fix.\n")
    (ex / "hidden" / "tests" / "test_example_hidden.py").write_text("def test_placeholder():\n    assert True\n")
    print("created repoevals.toml and .agent-evals/tasks/example_fix — add setup.patch or oracle.patch, then run "
          "`repoevals validate` and `repoevals check`")
    return 0


def cmd_list(args) -> int:
    cfg = _cfg(args)
    for t in _tasks(cfg, args.tasks):
        created = t.metadata.get("created", "-")
        print(f"{t.id:40s} {t.family:8s} {t.grader_type:14s} base={t.base[:12]} created={created}")
    return 0


def cmd_validate(args) -> int:
    from .check import validate_task
    from .graders import load_repo_graders
    cfg = _cfg(args)
    load_repo_graders(cfg.graders_dir)
    tasks = _tasks(cfg, args.tasks)
    src = Path(args.source).resolve() if args.source else None
    n_err = 0
    for t in tasks:
        for e in validate_task(t, cfg, src):
            warn = e.startswith("warning:")
            n_err += not warn
            print(f"{'WARN ' if warn else 'ERROR'} {t.id}: {e.removeprefix('warning: ')}")
    print(f"{len(tasks)} tasks, {n_err} errors")
    return 1 if n_err or not tasks else 0


def cmd_check(args) -> int:
    from .check import check_all
    cfg = _cfg(args)
    tasks = _tasks(cfg, args.tasks)
    res = check_all(tasks, cfg, _source(args, cfg), repeats=args.repeats,
                    keep=Path(args.keep).resolve() if args.keep else None)
    bad = 0
    for r in res:
        bad += r["status"] == "problem"
        print(f"{r['status'].upper():8s} {r['task']}: " + ("; ".join(r.get("problems", [])) or r.get("why", "")
                                                           or f"null={r.get('null')} oracle={r.get('oracle')}"))
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1))
    _step_summary("repoevals check", res)
    return 1 if bad else 0


def cmd_prepare(args) -> int:
    from .workspace import prepare
    cfg = _cfg(args)
    t = load_task(cfg.tasks_dir / args.task) if not Path(args.task).is_dir() else load_task(Path(args.task))
    meta = prepare(cfg, t, Path(args.run_dir).resolve(), _source(args, cfg), args.arm)
    print(json.dumps(meta, indent=1))
    return 0


def cmd_grade(args) -> int:
    from .grade import grade_run
    cfg = _cfg(args)
    g = grade_run(Path(args.run_dir).resolve(), cfg)
    print(json.dumps({k: g[k] for k in ("task", "status", "pass", "metrics", "errors")}, indent=1, default=str))
    return 0 if g["status"] != "error" else 2


def cmd_run(args) -> int:
    from .agents import record, run_agent
    from .grade import grade_run
    from .workspace import prepare
    cfg = _cfg(args)
    tasks = _tasks(cfg, args.tasks)
    out = Path(args.out or f"runs/{time.strftime('%Y%m%d-%H%M%S')}").resolve()
    image = args.docker or ""
    if image == "config":
        image = str(cfg.environment.get("image", ""))
        if not image:
            print("--docker without an image needs [environment].image in repoevals.toml")
            return 2
    src = _source(args, cfg)
    for t in tasks:
        for arm in args.arm or ["guidance:raw"]:
            for rep in range(args.repeats):
                rd = out / t.id / arm.replace(":", "_").replace("/", "_").replace("=", "_") / f"r{rep}"
                prepare(cfg, t, rd, src, arm)
                res = run_agent(args.agent, rd, model=args.model or "",
                                timeout=float(t.agent.get("timeout_sec", 3600)), docker=image,
                                network=str(cfg.environment.get("network", "none")),
                                extra_args=args.agent_args or "")
                record(rd, args.agent, args.model or "", res)
                from .workspace import update_meta
                update_meta(rd, repeat=rep)
                g = grade_run(rd, cfg, t)
                print(f"{t.id:36s} {arm:20s} r{rep} {g['status']:7s} {res.seconds:7.1f}s")
    print(f"runs in {out}; `repoevals report {out}`")
    return 0


def cmd_report(args) -> int:
    from .report import to_markdown, write_report
    root = Path(args.runs).resolve()
    s = write_report(root, Path(args.out or root / "report").resolve())
    md = to_markdown(s)
    print(md)
    _step_summary_text(md)
    return 0


def cmd_export(args) -> int:
    from .harbor import export_task
    cfg = _cfg(args)
    if args.format != "harbor":
        print("only `harbor` export is implemented")
        return 2
    for t in _tasks(cfg, args.tasks):
        print(export_task(t, cfg, Path(args.out).resolve()))
    return 0


def cmd_guidance_lint(args) -> int:
    from .guidance import lint
    cfg = _cfg(args)
    res = lint(cfg.root, cfg.guidance_globs)
    missing = [r for r in res if not r["exists"]]
    for r in missing:
        print(f"MISSING {r['file']}: `{r['claim']}`")
    print(f"{len(res)} path claims checked, {len(missing)} missing")
    return 1 if (missing and args.strict) else 0


def _step_summary(title: str, rows: list[dict]) -> None:
    lines = [f"### {title}", "", "| task | status | details |", "|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['task']} | {r['status']} | {'; '.join(r.get('problems', [])) or r.get('why', '')} |")
    _step_summary_text("\n".join(lines) + "\n")


def _step_summary_text(md: str) -> None:
    import os
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a") as fh:
            fh.write(md + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser("repoevals")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--config", help="path to repoevals.toml (default: search upward from cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    _add = sub.add_parser

    def add_parser(name, **kw):  # every subcommand also accepts --config (after the subcommand name)
        if kw.get("help"):
            kw.setdefault("description", kw["help"][0].upper() + kw["help"][1:] + ".")
        sp = _add(name, **kw)
        sp.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return sp
    sub.add_parser = add_parser  # type: ignore[method-assign]
    sub.add_parser("init", help="create repoevals.toml and an example task").set_defaults(fn=cmd_init)
    p = sub.add_parser("list", help="list tasks")
    p.add_argument("tasks", nargs="*", help=TASKS_HELP)
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("validate", help="static checks of task definitions")
    p.add_argument("tasks", nargs="*", help=TASKS_HELP)
    p.add_argument("--source", help="repository with history: also check that patches apply at each base")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("check", help="prove every task is valid: null run fails, reference passes")
    p.add_argument("tasks", nargs="*", help=TASKS_HELP)
    p.add_argument("--source", help=SOURCE_HELP)
    p.add_argument("--repeats", type=int, default=1, help="oracle repeats (flakiness)")
    p.add_argument("--keep", help="keep run directories here")
    p.add_argument("--json", help="write results as JSON")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("prepare", help="create a workspace for one task")
    p.add_argument("task", help="task id or task directory")
    p.add_argument("run_dir", help="run directory to create")
    p.add_argument("--arm", default="guidance:raw", help="guidance:raw | guidance:none | guidance:file=PATH")
    p.add_argument("--source", help=SOURCE_HELP)
    p.set_defaults(fn=cmd_prepare)
    p = sub.add_parser("grade", help="grade a run directory")
    p.add_argument("run_dir", help="run directory to grade (writes grade.json)")
    p.set_defaults(fn=cmd_grade)
    p = sub.add_parser("run", help="prepare, run an agent and grade, for tasks x arms x repeats")
    p.add_argument("tasks", nargs="*", help=TASKS_HELP)
    p.add_argument("--agent", required=True, help="claude-code | codex | noop | cmd:<template>")
    p.add_argument("--model", help="model id passed to the agent CLI")
    p.add_argument("--arm", action="append", help="guidance:raw | guidance:none | guidance:file=PATH (repeatable)")
    p.add_argument("--repeats", type=int, default=3, help="attempts per task and arm (default: 3)")
    p.add_argument("--docker", nargs="?", const="config", metavar="IMAGE",
                   help="run the agent in a container (default image: [environment].image; network: "
                        "[environment].network, default none)")
    p.add_argument("--agent-args", help="extra CLI args for the agent")
    p.add_argument("--source", help=SOURCE_HELP)
    p.add_argument("--out", help="output directory (default: runs/<timestamp>)")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("report", help="aggregate graded runs into a report")
    p.add_argument("runs", help="directory containing run directories (searched recursively)")
    p.add_argument("--out", help="report directory (default: <runs>/report)")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("export", help="export tasks to another format")
    p.add_argument("format", choices=["harbor"])
    p.add_argument("tasks", nargs="*", help=TASKS_HELP)
    p.add_argument("--out", required=True, help="output directory")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("guidance-lint", help="check that paths named in agent guidance files exist")
    p.add_argument("--strict", action="store_true", help="exit 1 when a claim is missing")
    p.set_defaults(fn=cmd_guidance_lint)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
