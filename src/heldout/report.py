"""Aggregate grade.json files into a Markdown report and machine-readable results.

    heldout report runs/2026-10-13/ --out report/
      report/summary.md        human-readable: per-arm pass rate with CIs, paired arm deltas, per-task table,
                               integrity flags, cost/tokens
      report/results.json      the same numbers, machine-readable
      report/results.jsonl     one flat record per graded run (easy to load into pandas / an eval database)
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .stats import cluster_bootstrap, paired_compare, pass_at_k, pass_hat_k


def load_grades(root: Path) -> list[dict]:
    out = []
    for p in sorted(root.rglob("grade.json")):
        # only grade.json written by heldout next to its meta.json, never files inside a workspace
        if not (p.parent / "meta.json").exists() or "workspace" in p.relative_to(root).parts[:-1]:
            continue
        try:
            g = json.loads(p.read_text())
        except ValueError:
            continue
        g["_run_dir"] = str(p.parent)
        out.append(g)
    return out


def summarize(grades: list[dict]) -> dict:
    by_arm: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    status = defaultdict(lambda: defaultdict(int))
    cost = defaultdict(float)
    tokens = defaultdict(int)
    integrity = []
    for g in grades:
        arm = f"{g.get('agent') or '?'}|{g.get('model') or '-'}|{g.get('arm') or '-'}"
        status[arm][g.get("status", "error")] += 1
        if g.get("pass") is not None:
            by_arm[arm][g["task"]].append(bool(g["pass"]))
        for m in ("f1", "recall", "kill_rate", "speedup", "case_pass_rate"):
            v = (g.get("metrics") or {}).get(m)
            if isinstance(v, (int, float)):
                scores[arm][f"{g['task']}:{m}"].append(float(v))
        pr = g.get("process") or {}
        cost[arm] += pr.get("cost_usd") or 0.0
        tokens[arm] += (pr.get("input_tokens") or 0) + (pr.get("output_tokens") or 0)
        integ = g.get("integrity") or {}
        if integ and not integ.get("integrity_ok", True):
            integrity.append({"task": g["task"], "arm": arm, "run": g.get("_run_dir"),
                              "checks": sorted({f["check"] for f in integ.get("integrity_findings", [])
                                                if f.get("violation")})})
    arms = {}
    for arm, res in by_arm.items():
        m, lo, hi = cluster_bootstrap(res)
        per_task = {}
        for t, v in sorted(res.items()):
            n, c = len(v), sum(v)
            per_task[t] = {"n": n, "pass": c, "rate": round(c / n, 3),
                           "pass@1": round(pass_at_k(n, c, 1), 3),
                           "pass@3": round(pass_at_k(n, c, 3), 3) if n >= 3 else None,
                           "pass^3": round(pass_hat_k(n, c, 3), 3) if n >= 3 else None}
        arms[arm] = {"mean_pass_rate": round(m, 4), "ci95": [round(lo, 4), round(hi, 4)], "n_tasks": len(res),
                     "n_runs": sum(len(v) for v in res.values()), "status": dict(status[arm]),
                     "cost_usd": round(cost[arm], 4), "tokens": tokens[arm], "tasks": per_task,
                     "scores": {k: round(sum(v) / len(v), 4) for k, v in sorted(scores[arm].items())}}
    for arm in status:
        arms.setdefault(arm, {"status": dict(status[arm]), "n_tasks": 0})
    names = sorted(by_arm)
    comparisons = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            comparisons.append({"a": a, "b": b, **paired_compare(by_arm[a], by_arm[b])})
    return {"schema": "heldout.report/v1", "arms": arms, "comparisons": comparisons, "integrity": integrity}


def to_markdown(s: dict) -> str:
    lines = ["# heldout report", "", "## Arms (agent | model | arm)", "",
             "| arm | tasks | runs | pass rate | 95% CI (task-clustered) | cost $ | tokens | status |",
             "|---|---|---|---|---|---|---|---|"]
    for arm, a in sorted(s["arms"].items()):
        ci = a.get("ci95", [None, None])
        lines.append(f"| `{arm}` | {a.get('n_tasks', 0)} | {a.get('n_runs', 0)} | {a.get('mean_pass_rate', '-')} | "
                     f"{ci[0]}–{ci[1]} | {a.get('cost_usd', 0)} | {a.get('tokens', 0)} | {a.get('status')} |")
    if s["comparisons"]:
        lines += ["", "## Paired comparisons (b − a, same tasks)", "",
                  "| a | b | tasks | Δ pass rate | 95% CI | p (sign-flip) | better / worse |", "|---|---|---|---|---|---|---|"]
        for c in s["comparisons"]:
            if c.get("n_tasks"):
                lines.append(f"| `{c['a']}` | `{c['b']}` | {c['n_tasks']} | {c['delta']:+} | {c['ci95'][0]}–{c['ci95'][1]} "
                             f"| {c['p_value']} | {c['tasks_better']} / {c['tasks_worse']} |")
    for arm, a in sorted(s["arms"].items()):
        if not a.get("tasks"):
            continue
        lines += ["", f"## Tasks — `{arm}`", "", "| task | runs | passed | pass@1 | pass@3 | pass^3 |",
                  "|---|---|---|---|---|---|"]
        for t, r in a["tasks"].items():
            lines.append(f"| {t} | {r['n']} | {r['pass']} | {r['pass@1']} | {r['pass@3']} | {r['pass^3']} |")
    if s["integrity"]:
        lines += ["", "## Integrity flags (read these transcripts)", ""]
        lines += [f"- `{i['task']}` `{i['arm']}`: {', '.join(i['checks'])} — {i['run']}" for i in s["integrity"]]
    lines += ["", "_Pass rates exclude skipped and errored (not graded) runs. CIs resample tasks, then trials._"]
    return "\n".join(lines) + "\n"


def write_report(root: Path, out_dir: Path) -> dict:
    grades = load_grades(root)
    s = summarize(grades)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(s, indent=1))
    (out_dir / "summary.md").write_text(to_markdown(s))
    with open(out_dir / "results.jsonl", "w") as fh:
        for g in grades:
            rec = {k: g.get(k) for k in ("task", "family", "grader", "agent", "model", "arm", "repeat", "status",
                                         "pass", "created", "heldout_version")}
            rec.update({f"m_{k}": v for k, v in (g.get("metrics") or {}).items()})
            rec.update({f"p_{k}": v for k, v in (g.get("process") or {}).items() if not isinstance(v, list)})
            rec["integrity_ok"] = (g.get("integrity") or {}).get("integrity_ok")
            fh.write(json.dumps(rec, default=str) + "\n")
    return s
