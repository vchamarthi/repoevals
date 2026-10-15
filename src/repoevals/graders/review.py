"""Code-review tasks. The change under review is committed on top of the base (review.patch); the agent reports
findings as a JSON array in its final answer:

    [{"file": "dpnp/x.py", "line": 42, "severity": "blocker|major|minor|nit", "rule": "...", "message": "..."}]

Two graders, both deterministic and both scoring precision as well as recall (recall alone rewards spamming):

type = "review_seeded"  manifest.json = {"seeds": [...], "decoys": [...]}
    seed  {id, class, file, lines, kw}  a planted or known problem. A finding matches a seed when it is in the
          same file ("*" = any file, then the keyword is required) and either its rule/message matches the
          regex `kw` while its line is within `kw_line_tol` (default 15) of a seed line, or its line is within
          `line_tol` (default 1) of a seed line. Matching is one-to-one (each finding credits at most one seed).
    decoy {id, file, lines, kw}         looks wrong, is acceptable here (project idiom). Flagging it is a false
          positive and is reported as decoys_flagged.
    No seeds = clean-change control: passes when there is no blocker/major finding and at most
    `max_findings_clean` (default 2) findings of any severity.

type = "review_replay"  gold.json = {"threads": [{file, line, body?}]} from the human review of a real PR
    A finding matches a thread when the file is equal and the line is within `line_tol` (default 5); one-to-one.

Both: findings beyond `max_findings` (default 2 * expected + 5) are ignored and flagged (`capped`).
Metrics: recall, precision (lower bound: unmatched findings may be real issues the manifest misses), f1.
Pass: gate metrics from [grader].gate, e.g. gate = ["recall>=0.6", "precision>=0.5", "decoys_flagged<=0"]
(seeded default: recall>=0.5, precision>=0.5, no decoys; replay default: score-only, pass = null).
"""
from __future__ import annotations

import json
import re

from ..util import last_json
from . import GradeContext, evaluate_gate

SEVERE = ("blocker", "major", "critical", "high")
DEFAULT_SEEDED_GATE = ["recall>=0.5", "precision>=0.5", "decoys_flagged<=0"]


def norm_path(p) -> str:
    p = str(p or "").strip().replace("\\", "/")
    for marker in ("/workspace/", "/repo/"):
        if marker in p:
            p = p.rsplit(marker, 1)[1]
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def parse_findings(answer: str) -> list[dict] | None:
    data = last_json(answer)
    if isinstance(data, dict):
        data = data.get("findings")
    if not isinstance(data, list):
        return None
    out = []
    for f in data:
        if not isinstance(f, dict):
            continue
        try:
            line = int(f.get("line") or -10**6)
        except (TypeError, ValueError):
            line = -10**6
        out.append({"file": norm_path(f.get("file")), "line": line,
                    "severity": str(f.get("severity", "")).lower(),
                    "text": " ".join(str(f.get(k, "")) for k in ("rule", "message", "evidence"))})
    return out


def _dist(line: int, lines: list[int]) -> int:
    return min((abs(line - x) for x in lines), default=10**9)


def _assign(cands: list[tuple[float, int, str]]) -> dict[int, str]:
    """Maximum one-to-one matching of findings to targets (augmenting paths); among maximum matchings, findings are
    tried in order of their best candidate score so the strongest evidence is matched first."""
    adj: dict[int, list[tuple[float, str]]] = {}
    for score, fi, tid in cands:
        adj.setdefault(fi, []).append((score, tid))
    for fi in adj:
        adj[fi].sort(key=lambda x: -x[0])
    owner: dict[str, int] = {}

    def augment(fi: int, seen: set) -> bool:
        for _, tid in adj[fi]:
            if tid in seen:
                continue
            seen.add(tid)
            if tid not in owner or augment(owner[tid], seen):
                owner[tid] = fi
                return True
        return False
    for fi in sorted(adj, key=lambda f: -adj[f][0][0]):
        augment(fi, set())
    return {fi: tid for tid, fi in owner.items()}


def gate_pass(out: dict, gate: list) -> bool | None:
    return evaluate_gate(out, gate)


def _prf(tp: int, n_expected: int, n_findings: int) -> dict:
    r = tp / n_expected if n_expected else None
    p = tp / n_findings if n_findings else (1.0 if n_expected == 0 else 0.0)
    f1 = (2 * p * r / (p + r)) if r is not None and (p + r) else (0.0 if r is not None else None)
    return {"recall": None if r is None else round(r, 3), "precision": round(p, 3),
            "f1": None if f1 is None else round(f1, 3)}


def g_review_seeded(ctx: GradeContext) -> dict:
    g = ctx.g
    man = json.loads((ctx.task.dir / g.get("manifest", "manifest.json")).read_text())
    seeds, decoys = man.get("seeds", []), man.get("decoys", [])
    findings = parse_findings(ctx.answer)
    if findings is None:
        return {"pass": False, "parse_error": True}
    cap = int(g.get("max_findings", 2 * len(seeds) + 5))
    capped = len(findings) > cap
    findings = findings[:cap]
    kw_tol, line_tol = int(g.get("kw_line_tol", 15)), int(g.get("line_tol", 1))
    cands = []
    for fi, f in enumerate(findings):
        mine = []
        if len(f["text"].strip()) < 8:  # a finding must say something to earn credit
            continue
        for s in seeds:
            same = s["file"] == "*" or f["file"] == norm_path(s["file"])
            if not same:
                continue
            kw = bool(s.get("kw")) and re.search(s["kw"], f["text"], re.I) is not None
            d = _dist(f["line"], s.get("lines", []))
            if s["file"] == "*":
                if kw:
                    mine.append((1.0, d, s["id"]))
            elif kw and (not s.get("lines") or d <= kw_tol):
                mine.append((2.0 - d / 1000, d, s["id"]))
            elif d <= line_tol:
                mine.append((1.0 - d / 1000, d, s["id"]))
        # a finding that names several problems is credited only for the one(s) nearest its line, so a single
        # keyword-stuffed finding (or copies of it) cannot collect many seeds
        if len(mine) > 1:
            best = min(d for _, d, _ in mine)
            mine = [m for m in mine if m[1] == best]
        cands += [(score, fi, sid) for score, _, sid in mine]
    hit = _assign(cands)
    flagged = set()
    for fi, f in enumerate(findings):
        if fi in hit:
            continue
        for d in decoys:
            if f["file"] == norm_path(d["file"]) and (
                    (d.get("kw") and re.search(d["kw"], f["text"], re.I)) or _dist(f["line"], d.get("lines", [])) <= 1):
                flagged.add(d["id"])
    by_class: dict[str, list[int]] = {}
    for s in seeds:
        c = by_class.setdefault(s.get("class", "default"), [0, 0])
        c[1] += 1
        c[0] += s["id"] in hit.values()
    n_severe = sum(f["severity"] in SEVERE for f in findings)
    out = {"n_findings": len(findings), "n_severe": n_severe, "capped": capped,
           "seeds_hit": sorted(hit.values()), "n_seeds": len(seeds),
           "decoys_flagged": sorted(flagged), "n_decoys": len(decoys),
           "unmatched": len(findings) - len(hit),
           "recall_by_class": {k: f"{a}/{b}" for k, (a, b) in by_class.items()},
           **_prf(len(hit), len(seeds), len(findings))}
    if not seeds:
        out["pass"] = n_severe == 0 and len(findings) <= int(g.get("max_findings_clean", 2)) and not capped
    else:
        out["pass"] = gate_pass(out, ctx.task.gate or DEFAULT_SEEDED_GATE)
    return out


def g_review_replay(ctx: GradeContext) -> dict:
    g = ctx.g
    gold = json.loads((ctx.task.dir / g.get("gold", "gold.json")).read_text())
    threads = gold.get("threads", gold if isinstance(gold, list) else [])
    findings = parse_findings(ctx.answer)
    if findings is None:
        return {"pass": False, "parse_error": True}
    cap = int(g.get("max_findings", 2 * len(threads) + 5))
    capped = len(findings) > cap
    findings = findings[:cap]
    tol = int(g.get("line_tol", 5))
    cands = []
    for fi, f in enumerate(findings):
        if len(f["text"].strip()) < 8:
            continue
        for ti, t in enumerate(threads):
            if f["file"] == norm_path(t.get("file")):
                d = abs(f["line"] - int(t.get("line") or -10**6))
                if d <= tol:
                    cands.append((1.0 - d / 1000, fi, str(ti)))
    hit = _assign(cands)
    out = {"n_findings": len(findings), "n_threads": len(threads), "capped": capped,
           "threads_hit": sorted(int(x) for x in hit.values()),
           **_prf(len(hit), len(threads), len(findings))}
    out["pass"] = gate_pass(out, ctx.task.gate)
    return out


def o_review(ctx: GradeContext) -> str:
    t = ctx.task
    if t.has("oracle_answer.json"):
        return "```json\n" + t.file("oracle_answer.json").read_text() + "\n```"
    if ctx.g.get("type") == "review_replay":
        gold = json.loads((t.dir / ctx.g.get("gold", "gold.json")).read_text())
        threads = gold.get("threads", gold if isinstance(gold, list) else [])
        ans = [{"file": th["file"], "line": th["line"], "severity": "major", "rule": "gold",
                "message": th.get("body", "")[:200]} for th in threads]
        return "```json\n" + json.dumps(ans) + "\n```"
    return "```json\n[]\n```"


def spam_answer(ctx: GradeContext) -> str:
    """Adversarial probe for `repoevals check`: one finding on every changed line of the review patch."""
    diff = ctx.tree().show("--unified=0", "--no-color", ctx.start_sha)
    findings, cur, line = [], None, 0
    for ln in diff.splitlines():
        if ln.startswith("+++ b/"):
            cur = ln[6:]
        elif ln.startswith("@@"):
            m = re.search(r"\+(\d+)", ln)
            line = int(m.group(1)) if m else 0
        elif ln.startswith("+") and cur:
            findings.append({"file": cur, "line": line, "severity": "minor", "rule": "x", "message": "possible problem on this line"})
            line += 1
    return "```json\n" + json.dumps(findings[:2000]) + "\n```"


GRADERS = {"review_seeded": g_review_seeded, "review_replay": g_review_replay}
ORACLES = {"review_seeded": o_review, "review_replay": o_review}
