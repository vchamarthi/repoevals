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
import re
import sys
import time
from pathlib import Path

from . import __version__
from .config import load_config
from .task import discover, load_task
from .util import git

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
library_globs = ["{lib}"]
protected_globs = []

[test]
adapter = "pytest"            # pytest | bazel | ctest | command
build = ""                    # e.g. "pip install -e . --no-build-isolation -q"
timeout_sec = 1800
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
    if args.preset:
        from .presets import preset_text
        text = preset_text(args.preset)
    else:
        pkgs = sorted(p.parent.name for p in root.glob("*/__init__.py") if p.parent.name not in ("tests", "test"))
        lib = [f"{p}/**" for p in pkgs] or [f"{root.name}/**"]
        text = INIT_TOML.replace("{name}", root.name).replace('["{lib}"]', json.dumps(lib))
    (root / "repoevals.toml").write_text(text)
    (root / ".agent-evals" / "tasks").mkdir(parents=True, exist_ok=True)
    print("created repoevals.toml and .agent-evals/tasks/\n"
          "next steps:\n"
          "  repoevals doctor                      check that the configuration works here\n"
          "  repoevals author mine                 find candidate tasks in your history\n"
          "  repoevals author fix <commit>         scaffold a task from one of them\n"
          "  repoevals author prompt --repo .      or let an AI agent author tasks for you")
    return 0


def cmd_list(args) -> int:
    cfg = _cfg(args)
    for t in _tasks(cfg, args.tasks):
        created = t.metadata.get("created", "-")
        print(f"{t.id:40s} {t.family:8s} {t.grader_type:14s} {t.status:6s} base={t.base[:12]} created={created}")
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
        from .util import package_versions
        Path(args.json).write_text(json.dumps({"tasks": res, "packages": package_versions(),
                                               "repoevals_version": __version__}, indent=1))
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
    from .runner import RunOptions, check_out_dir, default_runs_root, run_matrix
    cfg = _cfg(args)
    tasks = _tasks(cfg, args.tasks)
    src = _source(args, cfg)
    out = Path(args.out).expanduser().resolve() if args.out else \
        default_runs_root(cfg) / time.strftime("%Y%m%d-%H%M%S")
    problem = check_out_dir(out, cfg, src)
    if problem and not args.allow_out_in_repo:
        print(f"error: {problem}. Choose another --out (or pass --allow-out-in-repo).")
        return 2
    image = args.docker or ""
    if image == "config":
        image = str(cfg.environment.get("image", ""))
        if not image:
            print("--docker without an image needs [environment].image in repoevals.toml")
            return 2
    drafts = [t.id for t in tasks if t.status == "draft"]
    if drafts and not args.include_drafts:
        print(f"skipping {len(drafts)} draft task(s): {', '.join(drafts)} (use --include-drafts to run them)")
        tasks = [t for t in tasks if t.status != "draft"]
    if not tasks:
        print("no tasks to run")
        return 1
    opts = RunOptions(agent=args.agent, arms=args.arm or ["guidance:raw"], repeats=args.repeats,
                      model=args.model or "", docker=image, agent_args=args.agent_args or "", jobs=args.jobs,
                      resume=args.resume, retries=args.retries, max_cost=args.max_cost, label=args.label or "",
                      shuffle=args.shuffle, seed=args.seed)
    print(f"runs: {out}")
    m = run_matrix(cfg, tasks, src, out, opts)
    print(f"done: {m['counts']}" + (f"; stopped: {m['stopped_reason']}" if m.get("stopped_reason") else ""))
    print(f"next: repoevals report {out}")
    return 0


def cmd_report(args) -> int:
    from .report import to_markdown, write_report
    root = Path(args.runs).resolve()
    s = write_report(root, Path(args.out or root / "report").resolve(), args.compare or "")
    md = to_markdown(s)
    print(md)
    _step_summary_text(md)
    if args.fail_under is not None:
        low = [k for k, a in s["arms"].items() if a.get("mean_pass_rate") is not None
               and a["mean_pass_rate"] < args.fail_under]
        if low:
            print(f"pass rate below {args.fail_under}: {', '.join(low)}")
            return 1
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


def cmd_show(args) -> int:
    rd = Path(args.run_dir).resolve()
    g = json.loads((rd / "grade.json").read_text()) if (rd / "grade.json").exists() else {}
    meta = json.loads((rd / "meta.json").read_text()) if (rd / "meta.json").exists() else {}
    print(f"task      {g.get('task') or meta.get('task')}  ({g.get('family')}, grader {g.get('grader')})")
    print(f"config    agent={meta.get('agent')} model={meta.get('model')} arm={meta.get('arm')} "
          f"repeat={meta.get('repeat')} label={meta.get('label')}")
    print(f"agent     status={meta.get('agent_status')} {meta.get('agent_reason') or ''} "
          f"time={meta.get('agent_seconds')}s version={meta.get('agent_version')}")
    print(f"result    {g.get('status', 'not graded')}  pass={g.get('pass')}")
    for k, v in sorted((g.get("metrics") or {}).items()):
        print(f"  {k:28s} {v}")
    for k in ("tests", "regression", "baseline"):
        d = (g.get("details") or {}).get(k)
        if isinstance(d, dict) and d.get("status") != "passed":
            print(f"{k}: {d.get('status')} failing={d.get('failing')}\n{d.get('log_tail', '')[-1500:]}")
    pr = g.get("process") or {}
    if pr:
        print(f"process   commands={pr.get('n_commands')} failed={pr.get('n_command_fail')} edits={pr.get('n_edits')} "
              f"tokens={(pr.get('input_tokens') or 0) + (pr.get('output_tokens') or 0)} cost={pr.get('cost_usd')} "
              f"trace={pr.get('trace')} guidance_read={pr.get('guidance_read')}")
    for f in (g.get("integrity") or {}).get("integrity_findings", []):
        print(f"integrity {'VIOLATION' if f.get('violation') else 'note'} {f['check']}: {f.get('evidence', '')[:160]}")
    for e in g.get("errors", [])[:3]:
        print(f"error     {e.splitlines()[0] if e else e}")
    diff = rd / "agent.diff"
    if diff.exists():
        text = diff.read_text(errors="replace")
        files = re.findall(r"^diff --git a/(\S+)", text, re.M)
        print(f"diff      {len(files)} file(s): {', '.join(files[:20])}")
        if args.diff:
            print(text)
    if args.answer and (rd / "answer.txt").exists():
        print("answer:\n" + (rd / "answer.txt").read_text())
    return 0


def cmd_doctor(args) -> int:
    import shutil as sh

    from .adapters import get_adapter
    from .config import find_config
    ok = True

    def line(good: bool | None, msg: str) -> None:
        nonlocal ok
        mark = {True: "ok  ", False: "FAIL", None: "warn"}[good]
        ok &= good is not False
        print(f"{mark}  {msg}")
    cfgp = Path(args.config) if args.config else find_config(Path.cwd())
    line(bool(cfgp), f"config: {cfgp or 'repoevals.toml not found (run `repoevals init`)'}")
    cfg = _cfg(args)
    line(bool(sh.which("git")), "git on PATH")
    line(git(cfg.root, "rev-parse", "--git-dir").rc == 0, f"{cfg.root} is a git repository")
    shallow = git(cfg.root, "rev-parse", "--is-shallow-repository").out.strip() == "true"
    line(None if shallow else True, "full history" if not shallow else "shallow clone: task bases may be missing "
         "(git fetch --unshallow)")
    tasks = discover(cfg.tasks_dir)
    line(None if not tasks else True, f"{len(tasks)} task(s) in {cfg.tasks_dir}")
    adapter = get_adapter(cfg.test)
    line(True, f"test adapter: {cfg.test.adapter}")
    if args.tests:
        res = adapter.run(cfg.root, list(args.tests))
        line(res.status in ("passed", "failed"), f"test run on the current tree: {res.status} "
             f"({res.passed} passed, {res.failed} failed){'' if res.status != 'error' else ': ' + res.log[-300:]}")
    for agent, exe in (("claude-code", "claude"), ("codex", "codex")):
        line(None if not sh.which(exe) else True, f"{agent}: {sh.which(exe) or 'not installed'}")
    line(None if not sh.which("gh") else True, f"gh (review-replay authoring): {sh.which('gh') or 'not installed'}")
    line(None if not sh.which("docker") else True, f"docker: {sh.which('docker') or 'not installed'}")
    from .runner import default_runs_root
    from .workspace import ancestor_guidance
    root = default_runs_root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    found = ancestor_guidance(root)
    line(None if found else True, f"runs directory {root}" + (
        f": guidance outside the workspace (parent directories or user agent config) applies to every arm: "
        f"{', '.join(found)}" if found else ""))
    return 0 if ok else 1


def cmd_guidance(args) -> int:
    cfg = _cfg(args)
    if args.guidance_cmd == "lint":
        return cmd_guidance_lint(args)
    if args.guidance_cmd == "suggest":
        from .suggest import suggest
        runs = Path(args.runs).resolve()
        out = Path(args.out).resolve() if args.out else runs / "guidance"
        r = suggest(runs, cfg, out, draft_with=args.draft_with or "", model=args.model or "",
                    min_runs=args.min_runs)
        print(f"{r['evidence']['runs']} runs analysed; {len(r['suggestions'])} suggestion(s)")
        for s_ in r["suggestions"]:
            print(f"- {s_}")
        print(f"evidence: {out / 'evidence.md'}\ndraft ({r['drafted_by']}): {r['draft_dir']} "
              f"({', '.join(r['draft_files'])})\n"
              f"next: repoevals guidance verify {r['draft_dir']} --agent <agent> --repeats 5")
        return 0
    if args.guidance_cmd == "verify":
        from .report import load_grades, summarize, to_markdown
        from .runner import RunOptions, default_runs_root, run_matrix
        draft = Path(args.draft).resolve()
        if not draft.is_dir():
            print(f"error: {draft} is not a directory")
            return 2
        tasks = _tasks(cfg, args.tasks)
        if not args.include_drafts:
            tasks = [t for t in tasks if t.status != "draft"]
        if args.exclude_tasks_in:  # held-out verification: only tasks the evidence did not come from
            seen = {g.get("task") for g in load_grades(Path(args.exclude_tasks_in).resolve())}
            tasks = [t for t in tasks if t.id not in seen]
        if not tasks:
            print("no tasks to run (use --include-drafts for draft tasks)")
            return 1
        out = Path(args.out).resolve() if args.out else \
            default_runs_root(cfg) / f"guidance-verify-{time.strftime('%Y%m%d-%H%M%S')}"
        arms = [args.baseline, f"guidance:overlay={draft}"]
        opts = RunOptions(agent=args.agent, arms=arms, repeats=args.repeats, model=args.model or "",
                          jobs=args.jobs, retries=args.retries)
        print(f"runs: {out}")
        run_matrix(cfg, tasks, _source(args, cfg), out, opts)
        s = summarize(load_grades(out), compare="arm")
        md = to_markdown(s)
        (out / "verify.md").write_text(md)
        print(md)
        comp = next((c for c in s["comparisons"] if c.get("n_tasks")), None)
        if comp:
            draft_is_b = comp["b"].endswith(f"guidance:overlay={draft}")
            delta = comp["delta"] if draft_is_b else -comp["delta"]
            lo, hi = comp["ci95"] if draft_is_b else (-comp["ci95"][1], -comp["ci95"][0])
            verdict = "helps" if lo > 0 else ("hurts" if hi < 0 else "no clear difference")
            print(f"draft vs {args.baseline}: Δ pass rate {delta:+.3f} (95% CI {lo:+.3f} to {hi:+.3f}, "
                  f"p={comp['p_value']}): {verdict}")
        return 0
    return 2


def cmd_author(args) -> int:
    from . import authoring as A
    cfg = _cfg(args)
    src = _source(args, cfg)
    sub = args.author_cmd
    if sub == "mine":
        cands = A.mine(cfg, src, rev=args.rev, limit=args.limit, since=args.since or "")
        if args.release_base:
            cands = A.release_compatible(cfg, src, cands, args.release_base)
        if args.json:
            Path(args.json).write_text(json.dumps(cands, indent=1))
        for c in cands[: args.top]:
            pr = f"#{c['pr']}" if c["pr"] else c["sha"][:10]
            print(f"{c['score']:5.1f}  {c['date']}  {pr:>10s}  lib {c['lib_lines']:4d} lines/{len(c['lib_files'])} "
                  f"files  tests {len(c['test_files'])}  {c['subject'][:70]}")
        print(f"{len(cands)} candidates (commits changing library code and tests)")
        return 0
    if sub == "fix":
        d = A.scaffold_fix(cfg, src, args.commit, task_id=args.id or "", family=args.family, force=args.force,
                           base=args.base or "", extract_tests=args.extract_tests)
        print(f"created draft {d}\nnext: write instruction.md, narrow [grader].targets, add regression_targets, then "
              f"`repoevals check {d.name} --repeats 3`")
        return 0
    if sub == "review-replay":
        d = A.scaffold_replay(cfg, src, args.pr, repo_slug=args.repo or "", task_id=args.id or "", force=args.force,
                              target_base=args.base or "")
        print(f"created draft {d}\nnext: prune gold.json to substantive threads, then `repoevals check {d.name}`")
        return 0
    if sub == "mutants":
        task_dir = A.scaffold_mutation(cfg, src, args.task, args.file, args.function or "", base=args.base or "")
        items = A.mutants(src, args.file, function=args.function or "", max_n=args.max, base=args.base or "")
        for p in A.write_mutants(task_dir, items):
            print(p)
        print(f"{len(items)} mutants written; add reference tests in oracle_tests/, then "
              f"`repoevals author prune-mutants {args.task}`")
        return 0
    if sub == "prune-mutants":
        r = A.prune_mutants(cfg, src, args.task)
        print(json.dumps(r, indent=1))
        return 0 if r["kept"] else 1
    if sub == "pin":
        for tid, sha in A.pin(cfg, src, args.tasks or None):
            print(f"pinned {tid} to {sha[:12]}")
        return 0
    if sub == "approve":
        for tid in A.approve(cfg, args.tasks):
            print(f"approved {tid}")
        return 0
    if sub == "playbook":
        print(A.playbook_text())
        return 0
    if sub == "prompt":
        print(A.prompt_text(args.repo, args.count, args.families or "", args.workdir or "", args.playbook or ""))
        return 0
    if sub == "install":
        codex = Path(args.codex_home).expanduser() if args.codex else None
        for p in A.install_agent_files(cfg.root, claude=args.claude, codex_home=codex):
            print(f"wrote {p}")
        return 0
    return 2


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
    p = sub.add_parser("init", help="create repoevals.toml and the tasks directory")
    p.add_argument("--preset", help="start from a starter configuration, e.g. dpnp, mkl_fft, scikit-learn-intelex")
    p.set_defaults(fn=cmd_init)
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
    p.add_argument("--out", help="output directory (default: ~/.cache/repoevals/runs/<repo>/<timestamp>, "
                                 "or $REPOEVALS_RUNS_DIR)")
    p.add_argument("--allow-out-in-repo", action="store_true", help="allow --out inside the repository")
    p.add_argument("--include-drafts", action="store_true", help="also run tasks with status = \"draft\"")
    p.add_argument("--jobs", type=int, default=1, help="attempts to run in parallel (default: 1)")
    p.add_argument("--resume", action="store_true", help="skip attempts that already have a grade in --out")
    p.add_argument("--retries", type=int, default=2, help="retries after agent infrastructure errors (default: 2)")
    p.add_argument("--max-cost", type=float, help="stop starting new attempts after this many US dollars")
    p.add_argument("--label", help="label recorded with every result, e.g. a release or experiment name")
    p.add_argument("--shuffle", action="store_true", help="randomize the order of attempts")
    p.add_argument("--seed", type=int, default=0, help="random seed for --shuffle")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("report", help="aggregate graded runs into a report")
    p.add_argument("runs", help="directory containing run directories (searched recursively)")
    p.add_argument("--out", help="report directory (default: <runs>/report)")
    p.add_argument("--fail-under", type=float, help="exit 1 if any configuration's pass rate is below this")
    p.add_argument("--compare", choices=["arm", "label", "agent", "model"],
                   help="only compare configurations that differ in this one dimension")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("export", help="export tasks to another format")
    p.add_argument("format", choices=["harbor"])
    p.add_argument("tasks", nargs="*", help=TASKS_HELP)
    p.add_argument("--out", required=True, help="output directory")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("guidance-lint", help="check that paths named in agent guidance files exist")
    p.add_argument("--strict", action="store_true", help="exit 1 when a claim is missing")
    p.set_defaults(fn=cmd_guidance_lint)
    p = sub.add_parser("show", help="show one run: result, metrics, failing tests, integrity, agent diff")
    p.add_argument("run_dir", help="run directory")
    p.add_argument("--diff", action="store_true", help="print the agent's full diff")
    p.add_argument("--answer", action="store_true", help="print the agent's final answer")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("doctor", help="check that repoevals, git, the test setup and agent CLIs are ready")
    p.add_argument("tests", nargs="*", help="test targets to try running on the current tree")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("guidance", help="improve agent guidance files: evidence, drafts, and A/B verification")
    gsub = p.add_subparsers(dest="guidance_cmd", required=True)
    g = gsub.add_parser("suggest", help="mine graded runs for guidance problems; write evidence and a draft")
    g.add_argument("runs", help="directory with graded runs (ideally both guidance:raw and guidance:none)")
    g.add_argument("--out", help="output directory (default: <runs>/guidance)")
    g.add_argument("--draft-with", metavar="AGENT",
                   help="write the draft with an agent: claude-code | codex | cmd:<command reading the prompt on "
                        "stdin> (default: deterministic notes section)")
    g.add_argument("--model", help="model for --draft-with")
    g.add_argument("--min-runs", type=int, default=2, help="report problems seen in at least this many runs")
    g = gsub.add_parser("verify", help="run tasks with the current guidance and with a draft; compare")
    g.add_argument("draft", help="draft directory (overlaid on the workspace), e.g. <runs>/guidance/draft")
    g.add_argument("tasks", nargs="*", help=TASKS_HELP)
    g.add_argument("--agent", required=True, help="claude-code | codex | noop | cmd:<template>")
    g.add_argument("--model", help="model id passed to the agent CLI")
    g.add_argument("--baseline", default="guidance:raw", help="arm to compare against (default: guidance:raw)")
    g.add_argument("--repeats", type=int, default=5, help="attempts per task and arm (default: 5)")
    g.add_argument("--jobs", type=int, default=1, help="attempts to run in parallel")
    g.add_argument("--retries", type=int, default=2, help="retries after agent infrastructure errors")
    g.add_argument("--include-drafts", action="store_true", help="also use draft tasks")
    g.add_argument("--exclude-tasks-in", metavar="RUNS",
                   help="skip tasks that appear in these runs (verify on tasks the evidence did not come from)")
    g.add_argument("--out", help="output directory")
    g = gsub.add_parser("lint", help="check that paths named in agent guidance files exist")
    g.add_argument("--strict", action="store_true", help="exit 1 when a claim is missing")
    for x in gsub.choices.values():
        x.add_argument("--source", help=SOURCE_HELP)
    p.set_defaults(fn=cmd_guidance)
    p = sub.add_parser("author", help="create tasks: mine history, scaffold drafts, generate mutants, agent playbook")
    asub = p.add_subparsers(dest="author_cmd", required=True)
    a = asub.add_parser("mine", help="rank commits that changed library code together with tests")
    a.add_argument("--rev", default="HEAD", help="history to scan (default: HEAD)")
    a.add_argument("--limit", type=int, default=500, help="commits to scan (default: 500)")
    a.add_argument("--since", help="only commits after this date, e.g. 2025-01-01")
    a.add_argument("--top", type=int, default=30, help="candidates to print (default: 30)")
    a.add_argument("--json", help="write all candidates to this file")
    a.add_argument("--release-base", metavar="TAG",
                   help="only fixes made after TAG, in non-compiled code, that apply at TAG (use with fix --base TAG)")
    a = asub.add_parser("fix", help="scaffold a draft hidden-tests task from a commit")
    a.add_argument("commit", help="the commit that fixed the bug (or added the feature) with tests")
    a.add_argument("--id", help="task id (default: derived from the commit subject)")
    a.add_argument("--family", default="fix", choices=["fix", "feature"])
    a.add_argument("--base", help="start from this revision instead of the commit's parent (e.g. the last release)")
    a.add_argument("--extract-tests", action="store_true",
                   help="hide only the test functions the fix added or changed, in a new module (robust across bases)")
    a.add_argument("--force", action="store_true", help="overwrite an existing task directory")
    a = asub.add_parser("review-replay", help="scaffold a draft review-replay task from a GitHub pull request")
    a.add_argument("pr", type=int, help="pull request number")
    a.add_argument("--repo", help="owner/name on GitHub (default: from [repo].url or the origin remote)")
    a.add_argument("--id", help="task id (default: review_replay_pr<N>)")
    a.add_argument("--base", help="re-apply the reviewed change on this revision (e.g. a release) and remap lines")
    a.add_argument("--force", action="store_true", help="overwrite an existing task directory")
    a = asub.add_parser("mutants", help="generate candidate mutants for a Python file into a task")
    a.add_argument("task", help="task id (created if missing)")
    a.add_argument("file", help="repository-relative path of the Python file to mutate")
    a.add_argument("--function", help="only mutate this function")
    a.add_argument("--max", type=int, default=20, help="maximum mutants (default: 20)")
    a.add_argument("--base", help="mutate the file as of this revision (the task's base; default: working tree)")
    a = asub.add_parser("prune-mutants", help="drop mutants the task's reference tests do not detect")
    a.add_argument("task", help="task id")
    a = asub.add_parser("pin", help="replace symbolic task bases (HEAD, branches) with commit SHAs")
    a.add_argument("tasks", nargs="*", help="task ids (default: all)")
    a = asub.add_parser("approve", help="mark draft tasks as ready")
    a.add_argument("tasks", nargs="+", help="task ids")
    asub.add_parser("playbook", help="print the task-authoring playbook for AI agents")
    a = asub.add_parser("prompt", help="print a ready-to-use prompt for Claude Code, Codex or another agent")
    a.add_argument("--repo", required=True, help="repository URL or path to create tasks for")
    a.add_argument("--count", type=int, default=12, help="number of tasks (default: 12)")
    a.add_argument("--families", help="task mix, e.g. \"fix, review, test\"")
    a.add_argument("--workdir", help="where the agent should work")
    a.add_argument("--playbook", help="playbook location to reference (default: the published playbook URL)")
    a = asub.add_parser("install", help="install the playbook as a Claude Code skill and/or Codex prompt")
    a.add_argument("--claude", action=argparse.BooleanOptionalAction, default=True,
                   help="write .claude/skills/repoevals-task-author/SKILL.md (default: on)")
    a.add_argument("--codex", action="store_true", help="write a Codex custom prompt")
    a.add_argument("--codex-home", default="~/.codex", help="Codex home directory (default: ~/.codex)")
    for x in asub.choices.values():
        x.add_argument("--source", help=SOURCE_HELP)
    p.set_defaults(fn=cmd_author)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
