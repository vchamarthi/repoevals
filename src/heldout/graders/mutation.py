"""Test-writing tasks graded by mutation analysis: good tests fail on plausible faults.

[grader]
type = "mutation"
targets = []                    # optional fixed targets; else the agent's JSON answer {"targets": [...]};
                                # else the test files the agent added or changed
min_kill = 0.8                  # fraction of valid mutants the agent's tests must kill
oracle_min_kill = 1.0           # `heldout check`: the reference tests must kill at least this fraction
mutants_dir = "mutants"         # *.patch, each one fault in library code

Algorithm:
  1. library files the agent changed are reset to the starting commit (the task is to test, not to change code);
  2. the agent's tests must pass on the unmutated library (baseline);
  3. each mutant is applied, the targets re-run, the mutant reverted:
       failed      -> killed
       passed      -> survived
       build_error -> invalid (excluded: a compile/import error is not a test detecting a fault)
       timeout     -> killed (an infinite loop the tests exposed), reported separately
  4. kill_rate = killed / (killed + survived).

Reference solution: oracle_tests/ copied into the tree (answer gives targets when oracle_answer.json exists).
"""
from __future__ import annotations

from ..util import apply_patch, changed_files, copytree_overlay, glob_match, last_json, restore_paths
from . import GradeContext
from .tests import TreeGuard


def targets_for(ctx: GradeContext, changed: dict[str, str]) -> list[str]:
    if ctx.g.get("targets"):
        return list(ctx.g["targets"])
    ans = last_json(ctx.answer)
    if isinstance(ans, dict) and isinstance(ans.get("targets"), list) and ans["targets"]:
        return [str(t) for t in ans["targets"]]
    return sorted(p for p, s in changed.items() if s != "D" and glob_match(p, ctx.config.test_globs))


def g_mutation(ctx: GradeContext) -> dict:
    g, ws = ctx.g, ctx.workspace
    mutants = sorted((ctx.task.dir / g.get("mutants_dir", "mutants")).glob("*.patch"))
    if not mutants:
        raise FileNotFoundError(f"{ctx.task.id}: no mutants")
    ch = changed_files(ws, ctx.start_sha)
    targets = targets_for(ctx, ch)
    out: dict = {"targets": targets, "n_mutants": len(mutants)}
    if not targets:
        return {**out, "pass": False, "no_targets": True}
    guard = TreeGuard(ws)
    try:
        lib_edits = [p for p in ch if p not in targets and not glob_match(p, ctx.config.test_globs)
                     and (glob_match(p, ctx.config.library_globs) if ctx.config.library_globs else True)]
        out["library_reset"] = restore_paths(ws, ctx.start_sha, sorted(lib_edits))
        adapter = ctx.adapter()
        base = adapter.run(ws, targets)
        out["baseline"] = base.summary()
        out["baseline_pass"] = base.ok
        if not base.ok:
            out["pass"] = False
            return out
        per, killed, survived, invalid, timeouts = {}, 0, 0, 0, 0
        for m in mutants:
            ap = apply_patch(ws, m)
            if not ap.ok:
                per[m.stem] = "does_not_apply"
                invalid += 1
                continue
            r = adapter.run(ws, targets, build=bool(g.get("rebuild_per_mutant", True)))
            if r.status == "failed":
                per[m.stem], killed = "killed", killed + 1
            elif r.status == "timeout":
                per[m.stem], killed, timeouts = "killed_timeout", killed + 1, timeouts + 1
            elif r.status == "passed":
                per[m.stem], survived = "survived", survived + 1
            else:
                per[m.stem], invalid = f"invalid:{r.status}", invalid + 1
            apply_patch(ws, m, reverse=True)
        valid = killed + survived
        out.update(mutants=per, killed=killed, survived=survived, invalid=invalid, timeouts=timeouts,
                   kill_rate=round(killed / valid, 3) if valid else None)
        out["pass"] = bool(valid) and killed / valid >= float(g.get("min_kill", 0.8))
    finally:
        guard.restore()
    return out


def o_mutation(ctx: GradeContext) -> str | None:
    t = ctx.task
    src = t.dir / ctx.g.get("oracle_tests_dir", "oracle_tests")
    if not src.is_dir():
        raise FileNotFoundError(f"{t.id}: no oracle_tests/ directory")
    written = copytree_overlay(src, ctx.workspace)
    if t.has("oracle_answer.json"):
        return "```json\n" + t.file("oracle_answer.json").read_text() + "\n```"
    targets = [p for p in written if glob_match(p, ctx.config.test_globs)] or written
    import json
    return "```json\n" + json.dumps({"targets": targets}) + "\n```"


GRADERS = {"mutation": g_mutation}
ORACLES = {"mutation": o_mutation}
