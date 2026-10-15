"""`repoevals validate` (static, seconds) and `repoevals check` (executes graders, no LLM, no secrets).

validate — for every task: schema, known family/grader, referenced files exist, gate syntax, metadata needed for
contamination control (created date, source), instruction hygiene (no fix hints such as "FIXED in #123",
commit SHAs, or links to the fixing PR), and optionally that patches apply at the base (--source).

check — proves each task measures what it claims, per the Agentic Benchmark Checklist / Terminal-Bench practice:
  null      the untouched workspace (do-nothing agent) must NOT pass
  oracle    the reference solution must pass, `--repeats` times (flaky tasks are reported)
  probes    adversarial answers must NOT pass: findings spam for review tasks
  mutation  the reference tests must kill at least `oracle_min_kill` of the mutants
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from .config import RepoConfig
from .grade import apply_oracle, context, grade_run
from .graders import GRADERS, load_repo_graders
from .graders.review import spam_answer
from .task import FAMILIES, Task, host_supports
from .util import apply_patch, git
from .workspace import prepare

REQUIRED_FILES = {
    "review_seeded": [("manifest", "manifest.json"), ("review_patch", "review.patch")],
    "review_replay": [("gold", "gold.json"), ("review_patch", "review.patch")],
    "mutation": [("mutants_dir", "mutants")],
    "numeric": [("probe", "probe.py")],
}
HINT_PATTERNS = [
    r"\bFIXED in\b",
    r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{12,40}\b",  # commit SHAs (hex with letters and digits)
    r"/pull/\d+",
    r"/commit/[0-9a-f]+",
    r"\bthe fix (is|was) to\b",
]


def validate_task(t: Task, cfg: RepoConfig, source: Path | None = None) -> list[str]:
    errs: list[str] = []
    if t.family not in FAMILIES:
        errs.append(f"unknown family {t.family!r}")
    if not t.grader_type:
        errs.append("[grader].type missing")
    elif t.grader_type not in GRADERS:
        errs.append(f"unknown grader {t.grader_type!r}")
    if not t.instruction.strip():
        errs.append("empty instruction (instruction.md or prompt)")
    if "<!-- repoevals:draft" in t.instruction:
        errs.append("instruction.md is still the draft template")
    if any(str(x).startswith("TODO") for x in t.grader.get("targets", [])):
        errs.append("[grader].targets still contains TODO placeholders")
    is_tag = source is not None and git(source, "show-ref", "--verify", "--quiet", f"refs/tags/{t.base}").rc == 0
    if not re.fullmatch(r"[0-9a-f]{40}(\^+|~\d+)?", t.base) and not is_tag:
        errs.append(f"warning: base {t.base!r} is not a commit SHA; the task changes as the branch moves "
                    "(`repoevals author pin`)")
    if t.status == "draft":
        errs.append('warning: draft task (review it, then `repoevals author approve` or set status = "ready")')
    elif t.status != "ready":
        errs.append(f'unknown status {t.status!r} (use "draft" or "ready")')
    for key, default in REQUIRED_FILES.get(t.grader_type, []):
        if not (t.dir / t.grader.get(key, default)).exists():
            errs.append(f"missing {t.grader.get(key, default)}")
    if t.grader_type in ("hidden_tests", "numeric", "perf") and not (t.has("oracle.patch") or t.has("setup.patch")):
        errs.append("no reference solution (oracle.patch or setup.patch)")
    if t.grader_type == "hidden_tests" and not t.grader.get("targets"):
        errs.append("hidden_tests needs [grader].targets")
    if t.grader_type == "mutation" and not (t.dir / t.grader.get("oracle_tests_dir", "oracle_tests")).is_dir():
        errs.append("mutation task needs oracle_tests/")
    for g in t.gate:
        if not re.fullmatch(r"\s*\w+\s*((>=|<=|==|>|<)\s*[-\d.]+)?\s*", str(g)):
            errs.append(f"bad gate entry {g!r}")
    if t.grader_type.startswith("review") and t.gate and not any(
            str(g).strip().startswith(("precision", "f1", "decoys_flagged")) for g in t.gate):
        errs.append("warning: review gate has no precision/f1/decoy term; flooding findings can pass it")
    for f in ("manifest.json", "gold.json", "oracle_answer.json"):
        if t.has(f):
            try:
                json.loads(t.file(f).read_text())
            except ValueError as e:
                errs.append(f"{f} is not valid JSON: {e}")
    if not t.metadata.get("created"):
        errs.append("warning: [metadata].created missing (needed to filter by model training cutoff)")
    for pat in HINT_PATTERNS if "<!-- repoevals:draft" not in t.instruction else []:
        m = re.search(pat, t.instruction, re.I)
        if m:
            errs.append(f"warning: instruction may leak the fix: {m.group(0)!r}")
    if source is not None:
        from .util import Proc, resolve_rev
        try:
            base = Proc(0, resolve_rev(source, t.base, cfg.upstream))
        except RuntimeError:
            base = Proc(1, "")
        if base.rc != 0:
            errs.append(f"base {t.base!r} not found in {source} (shallow clone, or a fork without the upstream "
                        "tags? fetch them: git fetch <upstream-url> 'refs/tags/*:refs/tags/*')")
        else:
            with tempfile.TemporaryDirectory() as td:
                from .workspace import _extract
                _extract(source, base.out.strip(), Path(td))
                for name in ("setup.patch", "oracle.patch"):
                    if t.has(name) and not (name == "oracle.patch" and t.has("setup.patch")):
                        if not apply_patch(Path(td), t.file(name), check_only=True).ok:
                            errs.append(f"{name} does not apply at base")
    return errs


def _passed(g: dict) -> bool | None:
    return g.get("pass")


DETAIL_METRICS = ("hidden_pass", "regression_pass", "case_pass_rate", "n_cases", "recall", "precision", "kill_rate")


def _detail(g: dict) -> dict:
    """What a run did, for the author: key metrics and the failing-test summary (tests that crashed show rc < 0)."""
    d = {k: g["metrics"][k] for k in DETAIL_METRICS if k in g.get("metrics", {})}
    tests = (g.get("details") or {}).get("tests") or {}
    for k in ("passed", "failed", "rc", "failing_total", "failing_by_test"):
        if k in tests:
            d[k] = tests[k]
    return d


def check_task(t: Task, cfg: RepoConfig, source: Path, work: Path, repeats: int = 1) -> dict:
    ok, why = host_supports(t)
    res: dict = {"task": t.id, "grader": t.grader_type}
    if not ok:
        return {**res, "status": "skipped", "why": why}
    problems = []
    # 1. null agent
    rd = work / t.id / "null"
    prepare(cfg, t, rd, source)
    (rd / "answer.txt").write_text("")
    g = grade_run(rd, cfg, t)
    res["null"] = g["status"]
    res["null_detail"] = _detail(g)
    if g["status"] == "error":
        problems.append(f"null run not graded: {g['errors'][:1]}")
    elif g["pass"]:
        problems.append("untouched workspace passes (task measures nothing)")
    # 2. oracle, repeated
    outcomes = []
    for i in range(max(1, repeats)):
        rd = work / t.id / f"oracle{i}"
        prepare(cfg, t, rd, source)
        apply_oracle(rd, cfg)
        g = grade_run(rd, cfg, t)
        outcomes.append(g["status"])
        if t.grader_type == "mutation":
            kr = g["metrics"].get("kill_rate")
            res["oracle_kill_rate"] = kr
            need = float(t.grader.get("oracle_min_kill", 1.0))
            if kr is None or kr < need:
                problems.append(f"reference tests kill {kr} of mutants (< {need}); weak or invalid mutants")
        if t.grader_type.startswith("review") and g["metrics"].get("recall") not in (None, 1.0):
            problems.append(f"oracle answer recall {g['metrics'].get('recall')} != 1.0")
    res["oracle"] = outcomes
    res["oracle_detail"] = _detail(g)
    if any(o == "error" for o in outcomes):
        problems.append("oracle run not graded (see grade.json errors)")
    elif t.gate or t.grader_type not in ("review_replay",):
        if not all(o in ("pass", "scored") for o in outcomes):
            problems.append("reference solution does not pass" if "pass" not in outcomes else "flaky: oracle "
                            f"passed {outcomes.count('pass')}/{len(outcomes)}")
    # 3. adversarial probes
    if t.grader_type in ("review_seeded", "review_replay"):
        rd = work / t.id / "spam"
        prepare(cfg, t, rd, source)
        ctx = context(rd, cfg, t)
        (rd / "answer.txt").write_text(spam_answer(ctx))
        g = grade_run(rd, cfg, t)
        res["spam"] = {"status": g["status"], "precision": g["metrics"].get("precision")}
        if g["pass"]:
            problems.append("findings spam passes the review gate")
    res["problems"] = problems
    res["status"] = "ok" if not problems else "problem"
    return res


def check_all(tasks: list[Task], cfg: RepoConfig, source: Path, repeats: int = 1,
              keep: Path | None = None) -> list[dict]:
    load_repo_graders(cfg.graders_dir)
    work = keep or Path(tempfile.mkdtemp(prefix="repoevals-check-"))
    out = []
    try:
        for t in tasks:
            try:
                out.append(check_task(t, cfg, source, work, repeats))
            except Exception as e:
                out.append({"task": t.id, "status": "problem", "problems": [f"{type(e).__name__}: {e}"]})
    finally:
        if keep is None:
            shutil.rmtree(work, ignore_errors=True)
    return out
