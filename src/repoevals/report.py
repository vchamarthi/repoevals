"""Aggregate grade.json files into a Markdown report and machine-readable results.

    repoevals report runs/2026-10-13/ --out report/
      report/summary.md        human-readable: per-arm pass rate with CIs, paired arm deltas, per-task table,
                               integrity flags, cost/tokens
      report/results.json      the same numbers, machine-readable
      report/results.jsonl     one flat record per graded run (easy to load into pandas / an eval database)
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

from .stats import cluster_bootstrap, paired_compare, pass_at_k, pass_hat_k


def load_grades(root: Path) -> list[dict]:
    """Every grade.json under root that repoevals wrote next to a meta.json. Unreadable grades are returned as
    status "error" records instead of being dropped, so they are visible in the report."""
    out = []
    found = []
    for dirpath, dirnames, files in os.walk(root, followlinks=True):  # runs may be symlinked into a subset
        if "workspace" in dirnames:
            dirnames.remove("workspace")
        dirnames[:] = [d for d in dirnames if d != ".repoevals"]
        if "grade.json" in files and "meta.json" in files:
            found.append(Path(dirpath) / "grade.json")
    for p in sorted(found):
        try:
            g = json.loads(p.read_text())
        except (ValueError, OSError) as e:
            try:
                meta = json.loads((p.parent / "meta.json").read_text())
            except (ValueError, OSError):
                meta = {}
            g = {"task": meta.get("task", p.parent.name), "agent": meta.get("agent"), "model": meta.get("model"),
                 "arm": meta.get("arm"), "status": "error", "pass": None, "errors": [f"unreadable grade.json: {e}"]}
        g["_run_dir"] = str(p.parent)
        out.append(g)
    return out


def config_key(g: dict) -> str:
    """label|agent|model|arm; `cmd:` agents are shown as "cmd" (the command line is in meta.json)."""
    agent = g.get("agent") or "?"
    if agent.startswith("cmd:"):
        agent = "cmd"
    key = f"{agent}|{g.get('model') or '-'}|{g.get('arm') or '-'}"
    return f"{g['label']}|{key}" if g.get("label") else key


def _median(xs: list[float]) -> float | None:
    from statistics import median
    return round(median(xs), 1) if xs else None


DIMENSIONS = ("label", "agent", "model", "arm")


def _dims(key: str) -> dict:
    parts = key.split("|")
    if len(parts) == 3:
        parts = [""] + parts
    return dict(zip(DIMENSIONS, parts, strict=False))


def summarize(grades: list[dict], compare: str = "") -> dict:
    by_arm: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    by_family: dict[str, dict[str, dict[str, list[bool]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    status = defaultdict(lambda: defaultdict(int))
    cost: dict[str, float | None] = {}
    tokens = defaultdict(int)
    seconds = defaultdict(list)
    runs = defaultdict(int)
    integrity, partial_trace = [], set()
    for g in grades:
        arm = config_key(g)
        runs[arm] += 1
        status[arm][g.get("status", "error")] += 1
        if g.get("pass") is not None:
            by_arm[arm][g["task"]].append(bool(g["pass"]))
            by_family[arm][g.get("family") or "?"][g["task"]].append(bool(g["pass"]))
        for m in ("f1", "recall", "precision", "kill_rate", "speedup", "case_pass_rate"):
            v = (g.get("metrics") or {}).get(m)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                scores[arm][f"{g['task']}:{m}"].append(float(v))
        pr = g.get("process") or {}
        if pr.get("cost_usd") is not None:
            cost[arm] = (cost.get(arm) or 0.0) + float(pr["cost_usd"])
        else:
            cost.setdefault(arm, None)
        tokens[arm] += (pr.get("input_tokens") or 0) + (pr.get("output_tokens") or 0)
        if isinstance(g.get("agent_seconds"), (int, float)):
            seconds[arm].append(float(g["agent_seconds"]))
        if pr.get("trace") == "none":
            partial_trace.add(arm)
        integ = g.get("integrity") or {}
        if integ and not integ.get("integrity_ok", True):
            integrity.append({"task": g["task"], "arm": arm, "run": g.get("_run_dir"),
                              "checks": sorted({f["check"] for f in integ.get("integrity_findings", [])
                                                if f.get("violation")})})
    arms = {}
    for arm in sorted(runs):
        res = by_arm.get(arm, {})
        entry: dict = {"n_runs": runs[arm], "n_tasks": len(res), "status": dict(status[arm]),
                       "cost_usd": None if cost.get(arm) is None else round(cost[arm], 4),
                       "tokens": tokens[arm], "agent_seconds_median": _median(seconds[arm]),
                       "trace": "partial" if arm in partial_trace else "full",
                       "scores": {k: round(sum(v) / len(v), 4) for k, v in sorted(scores[arm].items())}}
        if res:
            m, lo, hi = cluster_bootstrap(res)
            per_task = {}
            for t, v in sorted(res.items()):
                n, c = len(v), sum(v)
                per_task[t] = {"n": n, "pass": c, "rate": round(c / n, 3), "pass@1": round(pass_at_k(n, c, 1), 3),
                               "pass@3": round(pass_at_k(n, c, 3), 3) if n >= 3 else None,
                               "pass^3": round(pass_hat_k(n, c, 3), 3) if n >= 3 else None}
            fams = {}
            for fam, fres in sorted(by_family[arm].items()):
                fm, flo, fhi = cluster_bootstrap(fres)
                fams[fam] = {"n_tasks": len(fres), "mean_pass_rate": round(fm, 4), "ci95": [round(flo, 4),
                                                                                            round(fhi, 4)]}
            entry.update(mean_pass_rate=round(m, 4), ci95=[round(lo, 4), round(hi, 4)], tasks=per_task,
                         families=fams)
        arms[arm] = entry
    names = sorted(by_arm)
    comparisons = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if compare:  # only pairs that differ in exactly that dimension
                da, db = _dims(a), _dims(b)
                if [d for d in DIMENSIONS if da[d] != db[d]] != [compare]:
                    continue
            comparisons.append({"a": a, "b": b, **paired_compare(by_arm[a], by_arm[b])})
    health = []
    all_tasks = sorted({t for res in by_arm.values() for t in res})
    for t in all_tasks:
        rates = [sum(res[t]) / len(res[t]) for res in by_arm.values() if t in res]
        if rates and all(r == 1.0 for r in rates):
            health.append({"task": t, "issue": "passes in every configuration"})
        elif rates and all(r == 0.0 for r in rates):
            health.append({"task": t, "issue": "fails in every configuration"})
    errors = defaultdict(int)
    for g in grades:
        if g.get("status") == "error":
            errors[g["task"]] += 1
    health += [{"task": t, "issue": f"{n} run(s) not graded (error)"} for t, n in sorted(errors.items())]
    return {"schema": "repoevals.report/v1", "arms": arms, "comparisons": comparisons, "integrity": integrity,
            "task_health": health}


def _dash(v) -> str:
    return "–" if v is None else str(v)


def _fmt_cost(v) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def to_markdown(s: dict) -> str:
    lines = ["# repoevals report", "", "## Configurations", "",
             "| configuration | tasks | runs | pass rate | 95% CI | median agent time (s) | cost $ | tokens | status |",
             "|---|---|---|---|---|---|---|---|---|"]
    for arm, a in sorted(s["arms"].items()):
        ci = a.get("ci95")
        ci_s = f"{ci[0]}–{ci[1]}" if ci else "–"
        rate = a.get("mean_pass_rate", "–")
        trace = " ¹" if a.get("trace") == "partial" else ""
        lines.append(f"| `{arm}`{trace} | {a.get('n_tasks', 0)} | {a.get('n_runs', 0)} | {rate} | {ci_s} | "
                     f"{_dash(a.get('agent_seconds_median'))} | {_fmt_cost(a.get('cost_usd'))} | "
                     f"{a.get('tokens', 0)} | {a.get('status')} |")
    if any(a.get("trace") == "partial" for a in s["arms"].values()):
        lines += ["", "¹ some runs have no agent trace (e.g. `cmd:` agents): trace-based integrity checks did not run."]
    fam_rows = [(arm, fam, f) for arm, a in sorted(s["arms"].items()) for fam, f in (a.get("families") or {}).items()]
    if fam_rows:
        lines += ["", "## By family", "", "| configuration | family | tasks | pass rate | 95% CI |",
                  "|---|---|---|---|---|"]
        lines += [f"| `{arm}` | {fam} | {f['n_tasks']} | {f['mean_pass_rate']} | {f['ci95'][0]}–{f['ci95'][1]} |"
                  for arm, fam, f in fam_rows]
    if s["comparisons"]:
        lines += ["", "## Paired comparisons (b − a, same tasks)", "",
                  "| a | b | tasks | Δ pass rate | 95% CI | p (sign-flip) | better / worse |",
                  "|---|---|---|---|---|---|---|"]
        for c in s["comparisons"]:
            if c.get("n_tasks"):
                ci = f"{c['ci95'][0]}–{c['ci95'][1]}"
                lines.append(f"| `{c['a']}` | `{c['b']}` | {c['n_tasks']} | {c['delta']:+} | {ci} "
                             f"| {c['p_value']} | {c['tasks_better']} / {c['tasks_worse']} |")
        if len(s["comparisons"]) > 1:
            lines += ["", f"_{len(s['comparisons'])} comparisons: p-values are not adjusted for multiple testing._"]
    for arm, a in sorted(s["arms"].items()):
        if not a.get("tasks"):
            continue
        lines += ["", f"## Tasks — `{arm}`", "", "| task | runs | passed | pass@1 | pass@3 | pass^3 |",
                  "|---|---|---|---|---|---|"]
        for t, r in a["tasks"].items():
            lines.append(f"| {t} | {r['n']} | {r['pass']} | {r['pass@1']} | {r['pass@3']} | {r['pass^3']} |")
    for arm, a in sorted(s["arms"].items()):
        if a.get("scores"):
            lines += ["", f"## Scores — `{arm}`", "", "| task:metric | mean |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in a["scores"].items()]
    if s.get("task_health"):
        lines += ["", "## Task health", ""]
        lines += [f"- `{h['task']}`: {h['issue']}" for h in s["task_health"]]
    if s["integrity"]:
        lines += ["", "## Integrity flags (read these transcripts)", ""]
        lines += [f"- `{i['task']}` `{i['arm']}`: {', '.join(i['checks'])} — {i['run']}" for i in s["integrity"]]
    lines += ["", "_Pass rates exclude skipped and errored (not graded) runs. CIs resample tasks, then trials._"]
    return "\n".join(lines) + "\n"


def write_report(root: Path, out_dir: Path, compare: str = "") -> dict:
    grades = load_grades(root)
    s = summarize(grades, compare)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(s, indent=1))
    (out_dir / "summary.md").write_text(to_markdown(s))
    with open(out_dir / "results.jsonl", "w") as fh:
        for g in grades:
            rec = {k: g.get(k) for k in ("task", "family", "grader", "label", "agent", "model", "arm", "repeat",
                                         "status", "pass", "created", "agent_status", "agent_seconds",
                                         "agent_version", "base_sha", "task_hash", "repoevals_version")}
            rec.update({f"m_{k}": v for k, v in (g.get("metrics") or {}).items()})
            rec.update({f"p_{k}": v for k, v in (g.get("process") or {}).items() if not isinstance(v, list)})
            rec["integrity_ok"] = (g.get("integrity") or {}).get("integrity_ok")
            fh.write(json.dumps(rec, default=str) + "\n")
    return s
