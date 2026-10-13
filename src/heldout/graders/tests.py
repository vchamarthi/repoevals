"""Fix / feature tasks graded by tests the agent never saw.

[grader]
type = "hidden_tests"
targets = ["tests/test_linalg.py::test_svd_fortran_order"]   # adapter targets that must pass after the change
regression_targets = ["tests/test_linalg.py"]                 # existing suites that must keep passing
root_cause_files = ["dpnp/linalg/dpnp_utils_linalg.py"]       # optional, reported only
gate = ["strict_pass"]                                        # metrics that must be truthy (default)
max_diff_lines = 400                                          # optional, adds metric diff_ok

Grading order:
  1. note which files the agent changed (vs. the starting commit), including new files;
  2. reset protected files (repo policy + [grader].protected) and, unless `allow_test_edits`, existing tests that
     the agent modified, so weakened tests cannot make a fix look good;
  3. overlay hidden/ onto the tree (files with an `.oracle` suffix lose it);
  4. build once, run targets, then regression targets;
  5. restore the tree to exactly what the agent left (regrading is reproducible).

Reference solution (`heldout check`): oracle.patch, else the reverse of setup.patch.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..util import apply_patch, changed_files, copytree_overlay, diff_stats, git, glob_match, restore_paths, run_shell
from . import GradeContext


class TreeGuard:
    """Snapshot of the agent's final tree; restores it after grading mutated the workspace."""

    def __init__(self, ws: Path):
        self.ws = ws
        self.tmp = Path(tempfile.mkdtemp(prefix="heldout-tree-"))
        shutil.copytree(ws, self.tmp / "t", symlinks=True)

    def restore(self) -> None:
        shutil.rmtree(self.ws)
        shutil.copytree(self.tmp / "t", self.ws, symlinks=True)
        shutil.rmtree(self.tmp, ignore_errors=True)


def changed_summary(ctx: GradeContext) -> dict:
    ch = changed_files(ctx.workspace, ctx.start_sha)
    added, removed = diff_stats(ctx.workspace, ctx.start_sha)
    tests = sorted(p for p in ch if glob_match(p, ctx.config.test_globs))
    return {"changed": ch, "files_changed": len(ch), "diff_lines": added + removed, "diff_added": added,
            "diff_removed": removed, "touched_tests": tests,
            "new_tests": sorted(p for p in tests if ch[p] == "A")}


def formatter_ok(ctx: GradeContext, files: list[str]) -> bool | None:
    if not ctx.config.formatter or not files:
        return None
    existing = [f for f in files if (ctx.workspace / f).is_file()]
    if not existing:
        return None
    return run_shell(ctx.config.formatter.replace("{files}", " ".join(existing)), cwd=ctx.workspace,
                     timeout=600).ok


def g_hidden_tests(ctx: GradeContext) -> dict:
    g, ws = ctx.g, ctx.workspace
    summary = changed_summary(ctx)
    ch = summary.pop("changed")
    out: dict = {**summary, "format_ok": formatter_ok(ctx, sorted(p for p, s in ch.items() if s != "D"))}
    if g.get("root_cause_files"):
        out["root_cause_touched"] = any(p in ch for p in g["root_cause_files"])
    if g.get("max_diff_lines"):
        out["diff_ok"] = out["diff_lines"] <= int(g["max_diff_lines"])
    guard = TreeGuard(ws)
    try:
        protected = list(ctx.config.protected_globs) + list(g.get("protected", []))
        reset = [p for p in ch if glob_match(p, protected)]
        if not g.get("allow_test_edits", False):
            reset += [p for p, s in ch.items() if s in ("M", "D") and glob_match(p, ctx.config.test_globs)]
        out["reset_before_grading"] = sorted(set(restore_paths(ws, ctx.start_sha, sorted(set(reset)))))
        hidden = ctx.task.dir / g.get("hidden_dir", "hidden")
        out["hidden_files"] = copytree_overlay(hidden, ws) if hidden.is_dir() else []
        adapter = ctx.adapter()
        res = adapter.run(ws, list(g.get("targets", [])), timeout=ctx.task.verifier.get("timeout_sec"))
        out["tests"] = res.summary()
        out["hidden_pass"] = res.ok
        if g.get("regression_targets"):
            reg = adapter.run(ws, list(g["regression_targets"]), build=False,
                              timeout=ctx.task.verifier.get("timeout_sec"))
            out["regression"] = reg.summary()
            out["regression_pass"] = reg.ok
        else:
            out["regression_pass"] = True
        out["build_ok"] = res.status not in ("build_error",)
        out["strict_pass"] = bool(out["hidden_pass"] and out["regression_pass"])
    finally:
        guard.restore()
    out["pass"] = all(bool(out.get(k)) for k in (ctx.task.gate or ["strict_pass"]))
    return out


def apply_reference(ctx: GradeContext) -> None:
    """Reference solution for a code-change task: oracle.patch, or the reverse of setup.patch."""
    t = ctx.task
    if t.has("oracle.patch"):
        p = apply_patch(ctx.workspace, t.file("oracle.patch"))
    elif t.has("setup.patch"):
        p = apply_patch(ctx.workspace, t.file("setup.patch"), reverse=True)
    else:
        raise FileNotFoundError(f"{t.id}: no oracle.patch or setup.patch to derive a reference solution from")
    if not p.ok:
        raise RuntimeError(f"{t.id}: reference patch does not apply: {p.out[:1000]}")


def o_hidden_tests(ctx: GradeContext) -> None:
    apply_reference(ctx)
    return None


def g_command(ctx: GradeContext) -> dict:
    """Build/setup tasks: run commands in the agent's tree; optionally require artifacts and an untouched tree.

    [grader]
    type = "command"
    commands = ["python -c 'import dpctl; dpctl.lsplatform()'"]   # all must exit 0
    artifacts = ["build/libfoo.so"]          # must exist (globs allowed); {answer.<key>} reads the agent's JSON
    artifact_kind = "elf"                    # optional magic check: elf | wheel | zip
    require_clean_tree = true                # agent may not edit tracked files outside allowed_globs
    allowed_globs = ["build/**"]
    """
    import json

    from ..util import last_json
    g, ws = ctx.g, ctx.workspace
    out: dict = {}
    ch = changed_files(ws, ctx.start_sha)
    edits = sorted(p for p in ch if not glob_match(p, g.get("allowed_globs", [])) and not
                   glob_match(p, ["**/__pycache__/**", "**/*.pyc", "build/**", "_skbuild/**", "*.egg-info/**"]))
    out["tree_edits"] = edits
    out["clean_tree"] = not edits
    answer = last_json(ctx.answer)
    answer = answer if isinstance(answer, dict) else {}
    results = []
    for cmd in g.get("commands", []):
        for k, v in answer.items():
            cmd = cmd.replace("{answer." + k + "}", str(v))
        p = run_shell(cmd, cwd=ws, timeout=ctx.task.verifier.get("timeout_sec", 1800), env=ctx.config.test.env)
        results.append({"command": cmd, "rc": p.rc, "seconds": round(p.seconds, 2), "log_tail": p.out[-2000:]})
    out["commands"] = results
    out["commands_ok"] = all(r["rc"] == 0 for r in results)
    arts_ok = True
    found = []
    for a in g.get("artifacts", []):
        for k, v in answer.items():
            a = a.replace("{answer." + k + "}", str(v))
        matches = [m for m in ws.glob(a) if m.is_file()] if not Path(a).is_absolute() else []
        if not matches:
            arts_ok = False
            continue
        for m in matches:
            head = m.read_bytes()[:4]
            kind = g.get("artifact_kind")
            good = (kind is None or (kind == "elf" and head == b"\x7fELF")
                    or (kind in ("wheel", "zip") and head[:2] == b"PK"))
            arts_ok &= good
            found.append({"path": m.relative_to(ws).as_posix(), "size": m.stat().st_size, "kind_ok": good})
    out["artifacts"] = found
    out["artifacts_ok"] = arts_ok
    gate = ctx.task.gate or (["commands_ok", "artifacts_ok"] + (["clean_tree"] if g.get("require_clean_tree") else []))
    out["pass"] = all(bool(out.get(k)) for k in gate)
    out["answer_keys"] = json.dumps(sorted(answer))
    return out


def o_command(ctx: GradeContext) -> str | None:
    t = ctx.task
    if t.has("oracle.patch") or t.has("setup.patch"):
        apply_reference(ctx)
    if t.has("oracle.sh"):
        p = run_shell(f"bash {t.file('oracle.sh')}", cwd=ctx.workspace, timeout=t.verifier.get("timeout_sec", 3600))
        if not p.ok:
            raise RuntimeError(f"{t.id}: oracle.sh failed: {p.out[-2000:]}")
    if t.has("oracle_answer.json"):
        return "```json\n" + t.file("oracle_answer.json").read_text() + "\n```"
    return None


GRADERS = {"hidden_tests": g_hidden_tests, "command": g_command}
ORACLES = {"hidden_tests": o_hidden_tests, "command": o_command}
