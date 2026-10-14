"""Evidence-based suggestions for agent guidance files (AGENTS.md, CLAUDE.md, Copilot instructions, ...).

    repoevals guidance suggest RUNS     mine graded runs for what agents struggled with; write evidence + a draft
    repoevals guidance verify DRAFT     run the same tasks with the current guidance and with the draft; compare

Mining is deterministic (no model):
  failing_commands   commands that failed in several runs (normalized: the tool and its first arguments)
  environment        error signatures in command output (missing modules, devices, compilers, permissions)
  exploration        files and lookups many runs spent time rediscovering before their first edit
  arm_regressions    tasks that do worse with the guidance than without it (needs both arms in RUNS)
  rule_violations    integrity violations in runs that had guidance (rules the agent did not follow)
  stale              paths named in guidance files that do not exist (guidance-lint)
  coverage           guidance files an agent would not read (e.g. only AGENTS.md, but Claude Code reads CLAUDE.md)

The draft is a directory overlaid on the workspace (arm `guidance:overlay=DIR`): deterministic by default (an
"Evaluation notes" section appended to AGENTS.md, mirrored into CLAUDE.md), or written by an agent CLI from the same
evidence (`--draft-with claude-code|codex|cmd:...`). `guidance verify` then measures whether the draft helps.
"""
from __future__ import annotations

import json
import re
import shlex
from collections import Counter, defaultdict
from pathlib import Path

from .config import RepoConfig
from .guidance import guidance_files, lint
from .report import load_grades
from .trace import read_events
from .util import glob_match, last_json, run

ENV_PATTERNS = [
    (r"ModuleNotFoundError: No module named '([\w.]+)'", "module {0} is not importable: say how the environment is "
                                                         "set up or which package provides it"),
    (r"command not found: (\S+)|(\S+): command not found", "`{0}` is not available: name the right tool or how to "
                                                           "install it"),
    (r"Default device is unavailable|No device of requested type|SyclDeviceCreationError",
     "no SYCL device was found: state the device selector to use (e.g. ONEAPI_DEVICE_SELECTOR=opencl:cpu)"),
    (r"icpx(: not found| is not found)|No CMAKE_CXX_COMPILER could be found",
     "the compiler is missing: say which tasks need a native build and how to avoid it"),
    (r"Permission denied", "a permission error occurred: document paths or commands to avoid"),
    (r"error: externally-managed-environment|Could not install packages",
     "package installation failed: say which environment to use and whether installing is allowed"),
    (r"ImportError: (.+)", "import error: {0}"),
]
EXPLORE = re.compile(r"^\s*(ls|find|tree|grep|rg|cat|head|tail|sed -n|git (ls-files|grep))\b")
AGENT_FILES = {"claude-code": "CLAUDE.md", "codex": "AGENTS.md", "copilot": ".github/copilot-instructions.md",
               "gemini": "GEMINI.md"}


def _norm_command(text: str, words: int = 3) -> str:
    """`cd x && FOO=1 python -m pytest -q tests/a.py::t` -> `python -m pytest`."""
    text = text.split("&&")[-1].split(";")[-1].strip()
    try:
        toks = shlex.split(text)
    except ValueError:
        toks = text.split()
    toks = [t for t in toks if not re.match(r"^\w+=", t)]
    out = []
    for t in toks:
        if t.startswith("-") and out and out[-1] not in ("python", "python3"):
            break
        if "/" in t and out:
            break
        out.append(t)
        if len(out) >= words:
            break
    return " ".join(out)


class Run:
    def __init__(self, grade: dict):
        self.grade = grade
        self.dir = Path(grade.get("_run_dir", "."))
        self.events = read_events(self.dir)
        meta_p = self.dir / "meta.json"
        self.meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}

    @property
    def arm(self) -> str:
        return self.grade.get("arm") or "-"

    @property
    def has_guidance(self) -> bool:
        return bool(self.meta.get("guidance")) or self.arm not in ("guidance:none", "none")


def analyze(runs_root: Path, cfg: RepoConfig, min_runs: int = 2) -> dict:
    runs = [Run(g) for g in load_grades(runs_root)]
    n = len(runs)
    fails: dict[str, set] = defaultdict(set)
    fail_samples: dict[str, str] = {}
    env: dict[str, set] = defaultdict(set)
    explore: dict[str, set] = defaultdict(set)
    violations: Counter = Counter()
    violation_runs: dict[str, list] = defaultdict(list)
    for r in runs:
        rid = str(r.dir)
        edited = False
        for e in r.events:
            kind, text = e.get("kind"), e.get("text") or ""
            if kind == "edit":
                edited = True
            if kind == "command":
                if e.get("rc") not in (0, None) or e.get("denied"):
                    key = _norm_command(text)
                    if key:
                        fails[key].add(rid)
                        fail_samples.setdefault(key, (e.get("output") or "")[-400:])
                if not edited and EXPLORE.match(text):
                    explore[f"$ {_norm_command(text, 2)} {text.split()[-1] if len(text.split()) > 1 else ''}"
                            .strip()].add(rid)
                out = e.get("output") or ""
                for pat, advice in ENV_PATTERNS:
                    m = re.search(pat, out)
                    if m:
                        groups = [g for g in m.groups() if g] if m.groups() else []
                        env[advice.format(*(groups or ["?"]))].add(rid)
            elif kind == "read" and not edited and e.get("path"):
                p = e["path"].split("/workspace/", 1)[-1]
                explore[p].add(rid)
        if r.has_guidance:
            for f in (r.grade.get("integrity") or {}).get("integrity_findings", []):
                if f.get("violation"):
                    violations[f["check"]] += 1
                    violation_runs[f["check"]].append(rid)
    by_task_arm: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in runs:
        if r.grade.get("pass") is not None:
            by_task_arm[r.grade["task"]][r.arm].append(bool(r.grade["pass"]))
    regressions = []
    for task, arms in sorted(by_task_arm.items()):
        none = arms.get("guidance:none")
        for arm, res in arms.items():
            if arm == "guidance:none" or not none:
                continue
            a, b = sum(res) / len(res), sum(none) / len(none)
            if a < b:
                regressions.append({"task": task, "arm": arm, "pass_rate": round(a, 2), "without": round(b, 2),
                                    "runs": len(res) + len(none)})
    present = [p.relative_to(cfg.root).as_posix() for p in guidance_files(cfg.root, cfg.guidance_globs)]
    names = {Path(p).name for p in present}
    coverage = []
    if "AGENTS.md" in names and "CLAUDE.md" not in names:
        coverage.append("AGENTS.md exists but CLAUDE.md does not: Claude Code may not read the guidance. Add a "
                        "CLAUDE.md that imports it (`@AGENTS.md`).")
    if "CLAUDE.md" in names and "AGENTS.md" not in names:
        coverage.append("CLAUDE.md exists but AGENTS.md does not: Codex and most other agents read AGENTS.md.")
    if not present:
        coverage.append("No agent guidance files exist. Start an AGENTS.md (and a CLAUDE.md that imports it).")
    agents = {r.grade.get("agent") for r in runs}
    for agent, file in AGENT_FILES.items():
        if agent in agents and not any(p.endswith(file) for p in present) and \
                not any(file in c for c in coverage):
            coverage.append(f"{agent} runs were evaluated, but {file} (which it reads) does not exist.")
    threshold = max(min_runs, 1)

    def ranked(d: dict[str, set], limit: int = 15) -> list[dict]:
        items = [(k, len(v)) for k, v in d.items() if len(v) >= threshold]
        return [{"item": k, "runs": c, "share": round(c / n, 2) if n else 0}
                for k, c in sorted(items, key=lambda x: (-x[1], x[0]))[:limit]]
    failing = ranked(fails)
    for f in failing:
        f["sample_output"] = fail_samples.get(f["item"], "")
    return {
        "schema": "repoevals.guidance-evidence/v1", "runs": n,
        "agents": sorted(a for a in agents if a), "arms": sorted({r.arm for r in runs}),
        "guidance_files": present,
        "failing_commands": failing,
        "environment": ranked(env),
        "exploration": ranked(explore, 10),
        "arm_regressions": regressions,
        "rule_violations": [{"check": k, "runs": v, "examples": violation_runs[k][:3]}
                            for k, v in violations.most_common()],
        "stale": [x for x in lint(cfg.root, cfg.guidance_globs) if not x["exists"]],
        "coverage": coverage,
    }


def suggestions(ev: dict) -> list[str]:
    """Deterministic, plain-language suggestions derived from the evidence."""
    out = []
    for f in ev["failing_commands"]:
        out.append(f"`{f['item']}` failed in {f['runs']} run(s): document the correct way to run it "
                   f"(arguments, environment variables, working directory).")
    for e in ev["environment"]:
        out.append(f"In {e['runs']} run(s): {e['item']}.")
    for x in ev["exploration"]:
        if x["share"] >= 0.5:
            out.append(f"{int(x['share'] * 100)}% of runs looked up `{x['item']}` before changing anything: say "
                       "where things are and what they are for, so agents do not have to rediscover it.")
    for r in ev["arm_regressions"]:
        out.append(f"Task `{r['task']}` passes less often with the guidance ({r['pass_rate']}) than without it "
                   f"({r['without']}): look for guidance that misleads on this kind of change.")
    for v in ev["rule_violations"]:
        out.append(f"`{v['check']}` happened in {v['runs']} run(s) despite the guidance: state the rule explicitly "
                   "and say why it matters.")
    for s in ev["stale"]:
        out.append(f"`{s['file']}` mentions `{s['claim']}`, which does not exist: update or remove it.")
    out += ev["coverage"]
    return list(dict.fromkeys(out))


def evidence_markdown(ev: dict, sugg: list[str]) -> str:
    lines = ["# Guidance evidence", "",
             f"{ev['runs']} graded runs; agents: {', '.join(ev['agents']) or '-'}; arms: {', '.join(ev['arms'])}.",
             f"Guidance files: {', '.join(ev['guidance_files']) or 'none'}.", "", "## Suggestions", ""]
    lines += [f"- {s}" for s in sugg] or ["- No recurring problems found."]

    def table(title: str, rows: list[dict], cols: list[str]) -> None:
        if rows:
            lines.extend(["", f"## {title}", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)])
            lines.extend("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|").replace("\n", " ")[:160]
                                           for c in cols) + " |" for r in rows)
    table("Failing commands", ev["failing_commands"], ["item", "runs", "share", "sample_output"])
    table("Environment problems", ev["environment"], ["item", "runs", "share"])
    table("Rediscovered before the first edit", ev["exploration"], ["item", "runs", "share"])
    table("Tasks worse with guidance", ev["arm_regressions"], ["task", "arm", "pass_rate", "without", "runs"])
    table("Rule violations with guidance", ev["rule_violations"], ["check", "runs"])
    table("Stale references", ev["stale"], ["file", "claim"])
    return "\n".join(lines) + "\n"


NOTES_HEADER = "## Notes from evaluation runs"


def deterministic_draft(cfg: RepoConfig, sugg: list[str], out: Path) -> list[str]:
    """Overlay directory: AGENTS.md (existing content + a notes section listing the evidence-backed points to
    address) and CLAUDE.md importing it if missing. A starting point for a human or an agent to rewrite."""
    out.mkdir(parents=True, exist_ok=True)
    agents_md = cfg.root / "AGENTS.md"
    base = agents_md.read_text() if agents_md.exists() else f"# Guidance for coding agents in {cfg.id}\n"
    if NOTES_HEADER in base:
        base = base.split(NOTES_HEADER)[0].rstrip() + "\n"
    notes = "\n".join(f"- TODO: {s}" for s in sugg) or "- No recurring problems found."
    (out / "AGENTS.md").write_text(base.rstrip() + f"\n\n{NOTES_HEADER}\n\n{notes}\n")
    written = ["AGENTS.md"]
    if not (cfg.root / "CLAUDE.md").exists():
        (out / "CLAUDE.md").write_text("@AGENTS.md\n")
        written.append("CLAUDE.md")
    return written


DRAFT_PROMPT = """You are improving the agent guidance files of the repository {repo} (files such as AGENTS.md,
CLAUDE.md, .github/copilot-instructions.md). Below is evidence from graded runs of coding agents on this repository,
then the current guidance files. Rewrite or extend the guidance so agents avoid the problems in the evidence.

Rules:
- Write durable project knowledge that helps on ANY future change: how to build, test and run things, where code
  lives, conventions and why they exist. The evidence comes from a fixed set of evaluation tasks: use it to find
  gaps, but never mention those tasks, the specific bugs or review findings they contain, or hints that would only
  help on them. Guidance tuned to the evaluation tasks would make the evaluation meaningless.
- Only add statements supported by the evidence or by the repository itself; be concrete (exact commands, paths,
  environment variables). Remove or fix stale references.
- Keep files short; do not repeat the same content in several files — CLAUDE.md may simply contain "@AGENTS.md".
- Do not modify any files. Answer with a fenced ```json block: an object mapping repository-relative paths to the
  complete new content of each guidance file you want to create or change.

# Evidence

{evidence}

# Current guidance files

{files}
"""


def llm_draft(cfg: RepoConfig, ev_md: str, out: Path, agent: str, model: str = "", timeout: float = 1800) -> list[str]:
    """Ask an agent CLI to write the revised guidance files (it reads only the evidence and the current files)."""
    files = "\n\n".join(f"## {p}\n\n```markdown\n{(cfg.root / p).read_text(errors='replace')}\n```"
                        for p in (x.relative_to(cfg.root).as_posix()
                                  for x in guidance_files(cfg.root, cfg.guidance_globs))) or "(none)"
    prompt = DRAFT_PROMPT.format(repo=cfg.id, evidence=ev_md, files=files)
    if agent == "claude-code":
        argv = ["claude", "-p", "--output-format", "text"] + (["--model", model] if model else [])
    elif agent == "codex":
        argv = ["codex", "exec", "-s", "read-only", "--skip-git-repo-check"] + (["-m", model] if model else []) + ["-"]
    elif agent.startswith("cmd:"):
        argv = shlex.split(agent[4:])
    else:
        raise ValueError(f"unknown drafting agent {agent!r}: claude-code | codex | cmd:<command reading stdin>")
    p = run(argv, cwd=cfg.root, timeout=timeout, stdin=prompt, scrub=True,
            keep_env=["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "HOME", "PATH"])
    data = last_json(p.out)
    if not isinstance(data, dict) or not data:
        raise RuntimeError(f"the drafting agent returned no JSON object of files (rc={p.rc}): {p.out[-800:]}")
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for rel, content in data.items():
        rel = str(rel).lstrip("/")
        if ".." in Path(rel).parts or not glob_match(rel, cfg.guidance_globs + ["CLAUDE.md", "AGENTS.md"]):
            continue  # only guidance files, never paths outside the overlay
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content))
        written.append(rel)
    if not written:
        raise RuntimeError("the drafting agent proposed no guidance files")
    return written


def suggest(runs_root: Path, cfg: RepoConfig, out: Path, draft_with: str = "", model: str = "",
            min_runs: int = 2) -> dict:
    ev = analyze(runs_root, cfg, min_runs)
    sugg = suggestions(ev)
    out.mkdir(parents=True, exist_ok=True)
    md = evidence_markdown(ev, sugg)
    (out / "evidence.json").write_text(json.dumps({**ev, "suggestions": sugg}, indent=1))
    (out / "evidence.md").write_text(md)
    draft_dir = out / "draft"
    written = llm_draft(cfg, md, draft_dir, draft_with, model) if draft_with else \
        deterministic_draft(cfg, sugg, draft_dir)
    return {"evidence": ev, "suggestions": sugg, "draft_dir": str(draft_dir), "draft_files": written,
            "drafted_by": draft_with or "deterministic"}
