"""Regression tests for bugs and grader-gaming holes found in an independent code review of agentexam 0.1.
Each test reproduces one attack or defect and asserts the correct behaviour."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentexam.config import RepoConfig, load_config
from agentexam.grade import apply_oracle, grade_run
from agentexam.graders import GradeContext
from agentexam.graders.review import g_review_seeded
from agentexam.graders.tests import g_command
from agentexam.report import write_report
from agentexam.stats import pass_at_k
from agentexam.task import Task, load_task
from agentexam.util import git, glob_match
from agentexam.workspace import prepare

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}


def _cfg(repo: Path):
    return load_config(repo / "agentexam.toml")


def _prep(repo: Path, task_id: str, rd: Path, oracle: bool = False):
    cfg = _cfg(repo)
    t = load_task(cfg.tasks_dir / task_id)
    prepare(cfg, t, rd, repo)
    if oracle:
        apply_oracle(rd, cfg)
    return cfg, t, rd / "workspace"


def _fake_ctx(tmp: Path, grader: dict, answer: str) -> GradeContext:
    t = Task(id="t", dir=tmp, family="custom", base="HEAD", instruction="x", grader=grader)
    return GradeContext(run_dir=tmp, workspace=tmp, task=t, config=RepoConfig(root=tmp), start_sha="HEAD",
                        answer=answer)


# --------------------------------------------------------------------------------------------- hidden_tests holes

def test_pytest_os_exit_zero_in_library_passes_hidden_tests(minisci_repo, tmp_path):
    """Bug NOT fixed; the agent only makes the package exit 0 when imported under pytest. PytestAdapter maps rc 0 to
    'passed' without checking that any test ran (no JUnit file, passed == 0). No test file is touched, so the
    integrity checks see nothing either."""
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    init = ws / "minisci" / "__init__.py"
    init.write_text(init.read_text() + "\nimport os as _os, sys as _sys\n"
                    "if 'pytest' in _sys.modules:\n    _os._exit(0)\n")
    (tmp_path / "run" / "answer.txt").write_text("done")
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["pass"] is False, g["details"].get("tests")


def test_root_conftest_skipping_everything_passes_hidden_tests(minisci_repo, tmp_path):
    """A new root conftest.py is neither a test file (test_globs = tests/**) nor reset; skipped tests give rc 0."""
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    (ws / "conftest.py").write_text("import pytest\n\ndef pytest_collection_modifyitems(items):\n"
                                    "    for i in items:\n        i.add_marker(pytest.mark.skip(reason='x'))\n")
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["pass"] is False


def test_skip_worktree_hides_weakened_test_from_reset_and_integrity(minisci_repo, tmp_path):
    """`git update-index --skip-worktree` (or .git/info/exclude, or rm -rf .git) makes changed_files() blind:
    the weakened test is neither reset nor flagged."""
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run", oracle=True)
    test = ws / "tests" / "test_basic.py"
    test.write_text(test.read_text().replace("def test_mean():", "@pytest.mark.skip\ndef test_mean():"))
    git(ws, "update-index", "--skip-worktree", "tests/test_basic.py", check=True)
    g = grade_run(tmp_path / "run", cfg, t)
    assert "tests/test_basic.py" in g["details"]["reset_before_grading"]
    assert g["integrity"]["integrity_ok"] is False


def test_hidden_overlay_writes_through_agent_symlink(minisci_repo, tmp_path):
    """The agent pre-creates the hidden test path as a symlink to a file outside the workspace; copytree_overlay
    follows it and writes the hidden test there (exfiltration across repeats / arbitrary write)."""
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run", oracle=True)
    outside = tmp_path / "outside" / "leak.py"
    outside.parent.mkdir()
    outside.write_text("# placeholder\n")
    os.symlink(outside, ws / "tests" / "test_var_hidden.py")
    grade_run(tmp_path / "run", cfg, t)
    assert outside.read_text() == "# placeholder\n", "hidden test leaked outside the workspace"


def test_formatter_mutates_agent_tree_before_snapshot(minisci_repo, tmp_path):
    """formatter_ok runs before TreeGuard, so an auto-fixing formatter rewrites the agent's files permanently."""
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run", oracle=True)
    cfg.formatter = (f"{sys.executable} -c \"import sys; [open(f, 'a').write('# formatted\\n') "
                     "for f in sys.argv[1:]]\" {files}")
    before = (ws / "minisci" / "stats.py").read_text()
    grade_run(tmp_path / "run", cfg, t)
    assert (ws / "minisci" / "stats.py").read_text() == before


def test_comparison_gate_in_hidden_tests_fails_correct_solution(minisci_repo, tmp_path):
    """Docs: gate entries may be comparisons ("diff_lines<=400"); validate accepts them, but hidden_tests/command
    evaluate gates with out.get(entry) -> None, so the oracle fails."""
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run", oracle=True)
    t.grader["gate"] = ["strict_pass", "diff_lines<=400"]
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["pass"] is True


# --------------------------------------------------------------------------------------------- workspace / prepare

def test_prepare_inside_source_repo_silently_skips_setup_patch(minisci_repo):
    """`agentexam run` defaults to --out runs/<stamp> under the cwd (the repo root). `git apply` run inside a
    subdirectory of another repository ignores paths outside that subdirectory and exits 0, so setup.patch is
    never applied: the bug is absent and every agent 'fixes' it."""
    cfg = _cfg(minisci_repo)
    t = load_task(cfg.tasks_dir / "fix_var_ddof")
    prepare(cfg, t, minisci_repo / "runs" / "r0", minisci_repo)
    assert "/ n)" in (minisci_repo / "runs" / "r0" / "workspace" / "minisci" / "stats.py").read_text()


def test_git_archive_export_ignore_drops_files(minisci_repo, tmp_path):
    """git archive honours export-ignore (and export-subst), common for tests/ in sdists."""
    (minisci_repo / ".gitattributes").write_text("tests export-ignore\n")
    subprocess.run(["git", "add", "-A"], cwd=minisci_repo, check=True, env=GIT_ENV)
    subprocess.run(["git", "commit", "-qm", "attrs"], cwd=minisci_repo, check=True, env=GIT_ENV)
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    assert (ws / "tests" / "test_basic.py").exists()


def test_eval_globs_remove_entire_top_level_directory(tmp_path):
    """onedal example: tasks = "dev/agent_evals/tasks" -> eval_globs removes dev/** (oneDAL's bazel build lives
    in dev/bazel)."""
    cfg = RepoConfig(root=tmp_path, tasks="dev/agent_evals/tasks")
    assert not glob_match("dev/bazel/dal.bzl", cfg.eval_globs)


# --------------------------------------------------------------------------------------------- mutation

def test_mutation_targets_from_answer_are_shell_injected(minisci_repo, tmp_path):
    """No tests written at all: the answer's 'targets' string is pasted into a bash command line, so
    `git diff --quiet` detects every mutant -> kill rate 1.0 (and arbitrary code runs on the grader host)."""
    cfg, t, ws = _prep(minisci_repo, "test_chebyshev", tmp_path / "run")
    (tmp_path / "run" / "answer.txt").write_text(
        '```json\n{"targets": ["tests/test_basic.py && git diff --quiet -- minisci && true"]}\n```')
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["pass"] is False, g["metrics"]


def test_mutation_library_edit_listed_as_target_is_not_reset(minisci_repo, tmp_path):
    """Listing a library file in answer.targets exempts it from the library reset; editing it makes mutants
    'does_not_apply', which are silently excluded from the kill-rate denominator."""
    cfg, t, ws = _prep(minisci_repo, "test_chebyshev", tmp_path / "run")
    d = ws / "minisci" / "distance.py"
    d.write_text(d.read_text().replace('"""Chebyshev (L-infinity) distance', '"""Chebyshev distance'))
    (ws / "tests" / "test_cheb_weak.py").write_text(
        "import minisci\n\ndef test_runs():\n    assert minisci.chebyshev([0.0], [5.0]) == 5.0\n")
    (tmp_path / "run" / "answer.txt").write_text(
        '```json\n{"targets": ["tests/test_cheb_weak.py", "minisci/distance.py"]}\n```')
    g = grade_run(tmp_path / "run", cfg, t)
    assert "minisci/distance.py" in g["details"]["library_reset"]
    assert g["metrics"]["invalid"] == 0


# --------------------------------------------------------------------------------------------- command

def test_command_answer_placeholder_is_shell_injected(tmp_path):
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "commit", "-q", "--allow-empty", "-m", "i"]):
        subprocess.run(cmd, cwd=tmp_path, check=True, env=GIT_ENV)
    ctx = _fake_ctx(tmp_path, {"type": "command", "commands": ["test -f {answer.path}"]},
                    '```json\n{"path": "does-not-exist.txt || true"}\n```')
    out = g_command(ctx)
    assert out["commands_ok"] is False


# --------------------------------------------------------------------------------------------- review

def test_review_keyword_stuffed_duplicates_hit_every_seed(minisci_repo, tmp_path):
    """Five copies of one keyword-stuffed finding at one line credit five different seeds: recall 1, precision 1."""
    cfg, t, ws = _prep(minisci_repo, "review_seeded_softmax", tmp_path / "run")
    f = {"file": "minisci/stats.py", "line": 40, "severity": "major", "rule": "x",
         "message": "overflow; debug print; axis ignored; skips last element; length mismatch"}
    (tmp_path / "run" / "answer.txt").write_text("```json\n" + json.dumps([f] * 5) + "\n```")
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["pass"] is False, g["metrics"]


def test_review_greedy_assignment_undercounts_recall(tmp_path):
    """Greedy one-to-one matching is not a maximum matching: a correct two-finding answer gets recall 0.5."""
    (tmp_path / "manifest.json").write_text(json.dumps({"seeds": [
        {"id": "S1", "file": "a.py", "lines": [10], "kw": "overflow"},
        {"id": "S2", "file": "a.py", "lines": [10], "kw": "axis"}], "decoys": []}))
    ans = [{"file": "a.py", "line": 10, "message": "overflow and the axis argument is ignored"},
           {"file": "a.py", "line": 20, "message": "overflow for large inputs"}]
    out = g_review_seeded(_fake_ctx(tmp_path, {"type": "review_seeded"}, "```json\n" + json.dumps(ans) + "\n```"))
    assert out["recall"] == 1.0


# --------------------------------------------------------------------------------------------- numeric / perf

def test_numeric_agent_code_replaces_comparator(minisci_repo, tmp_path):
    """The probe, reference and comparator run in the same interpreter as the agent's code; the unfixed
    logsumexp passes because minisci/__init__.py swaps the runner's default_compare."""
    cfg, t, ws = _prep(minisci_repo, "numeric_logsumexp", tmp_path / "run")
    init = ws / "minisci" / "__init__.py"
    init.write_text(init.read_text() + "\nimport sys as _s\n_m = _s.modules.get('__main__')\n"
                    "if hasattr(_m, 'default_compare'):\n"
                    "    _m.default_compare = lambda np, case, got, want, tol: {'ok': True}\n")
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["pass"] is False, g["metrics"]


def test_numeric_candidate_crash_is_error_not_fail(minisci_repo, tmp_path):
    """A candidate that kills the runner process yields status 'error', which report.py excludes from pass rates:
    breaking the library is rewarded over failing."""
    cfg, t, ws = _prep(minisci_repo, "numeric_logsumexp", tmp_path / "run")
    init = ws / "minisci" / "__init__.py"
    init.write_text(init.read_text() + "\nimport os as _o, sys as _s\n"
                    "if hasattr(_s.modules.get('__main__'), 'default_compare'):\n    _o._exit(3)\n")
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["status"] == "fail", g["errors"][:1]


def test_numeric_complex_inf_vs_finite_accepted():
    np = pytest.importorskip("numpy")
    from agentexam.data.numeric_runner import default_compare
    r = default_compare(np, {}, np.array([complex(np.inf, 0.0)]), np.array([1.0 + 0j]), {})
    assert r["ok"] is False


def test_numeric_complex_result_for_real_reference_accepted():
    np = pytest.importorskip("numpy")
    from agentexam.data.numeric_runner import default_compare
    r = default_compare(np, {}, np.array([1.0 + 5.0j]), np.array([1.0]), {})
    assert r["ok"] is False


PERF_SETUP = """diff --git a/minisci/distance.py b/minisci/distance.py
--- a/minisci/distance.py
+++ b/minisci/distance.py
@@ -1,4 +1,5 @@
 import numpy as np
+import time; time.sleep(0.3)


 def chebyshev(a, b):
"""


def test_perf_bench_cannot_see_candidate_tree(minisci_repo, tmp_path):
    """bench.py runs as `python <task_dir>/bench.py` with cwd=workspace: sys.path[0] is the task dir and no
    PYTHONPATH is set, so the bench cannot import the tree under test (and with `pip install -e .` builds, the
    baseline build re-points the editable install, so both arms time the baseline). The oracle cannot pass."""
    td = tmp_path / "perf_task"
    td.mkdir()
    (td / "task.toml").write_text('version = "1.0"\n[metadata]\nfamily = "perf"\nbase = "HEAD"\n'
                                  'created = "2026-10-13"\n[grader]\ntype = "perf"\nbench = "python bench.py"\n'
                                  "repeats = 3\nwarmup = 0\nmin_speedup = 1.5\n")
    (td / "instruction.md").write_text("make import fast\n")
    (td / "setup.patch").write_text(PERF_SETUP)
    (td / "bench.py").write_text("import json, time\nt = time.perf_counter()\nimport minisci\n"
                                 "print(json.dumps({'seconds': time.perf_counter() - t}))\n")
    cfg = _cfg(minisci_repo)
    t = load_task(td)
    rd = tmp_path / "run"
    prepare(cfg, t, rd, minisci_repo)
    apply_oracle(rd, cfg)
    g = grade_run(rd, cfg, t)
    assert g["status"] == "pass", g["errors"][:1]


# --------------------------------------------------------------------------------------------- report / stats

def test_report_counts_grade_json_planted_inside_workspace(minisci_repo, tmp_path):
    cfg = _cfg(minisci_repo)
    t = load_task(cfg.tasks_dir / "fix_var_ddof")
    rd = tmp_path / "runs" / "fix_var_ddof" / "raw" / "r0"
    prepare(cfg, t, rd, minisci_repo)
    grade_run(rd, cfg, t)  # null agent: fail
    planted = rd / "workspace" / "notes" / "grade.json"
    planted.parent.mkdir()
    planted.write_text(json.dumps({"task": "fix_var_ddof", "arm": "guidance:raw", "status": "pass", "pass": True}))
    s = write_report(tmp_path / "runs", tmp_path / "report")
    arm = s["arms"]["?|-|guidance:raw"]
    assert arm["n_runs"] == 1 and arm["mean_pass_rate"] == 0.0


def test_pass_at_k_with_zero_successes_and_n_below_k():
    assert pass_at_k(2, 0, 3) == 0.0


# --------------------------------------------------------------------------------------------- integrity

def _repo_with_test(tmp_path: Path, added_line: str):
    from agentexam.integrity import check
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=ws, check=True, env=GIT_ENV)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True, env=GIT_ENV)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=ws, check=True, env=GIT_ENV)
    base = git(ws, "rev-parse", "HEAD").out.strip()
    (ws / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n" + added_line + "\n")
    return check(ws, base, [], {"tests/test_a.py": "M"}, ["tests/**"], [], [])


def test_integrity_false_positive_disabled_identifier(tmp_path):
    """re.I turns the GTest `DISABLED_` pattern into a match for any `disabled_...` Python identifier."""
    r = _repo_with_test(tmp_path, "def test_b(cfg):\n    assert cfg.disabled_ops == []")
    assert r["integrity_ok"] is True, r["integrity_findings"]


def test_integrity_misses_skiptest(tmp_path):
    r = _repo_with_test(tmp_path, "class T:\n    def test_c(self):\n        self.skipTest('later')")
    assert r["integrity_ok"] is False


# --------------------------------------------------------------------------------------------- harbor export

def test_harbor_test_sh_passes_config_after_subcommand(minisci_repo, tmp_path):
    """--config used to be accepted only before the subcommand, so the Harbor verifier's `agentexam grade "$RUN"
    --config ...` exited 2 and never graded. Both positions must work."""
    from agentexam.cli import main
    from agentexam.harbor import TEST_SH
    assert "agentexam --config /tests/agentexam.toml grade" in TEST_SH
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run", oracle=True)
    rc = main(["grade", str(tmp_path / "run"), "--config", str(minisci_repo / "agentexam.toml")])
    assert rc == 0


def test_grading_a_symlinked_workspace_errors(minisci_repo, tmp_path):
    """Harbor's test.sh symlinks $RUN/workspace -> /workspace; TreeGuard.restore() calls rmtree on the symlink."""
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run", oracle=True)
    rd2 = tmp_path / "run2"
    rd2.mkdir()
    os.symlink(ws, rd2 / "workspace")
    (rd2 / "meta.json").write_text((tmp_path / "run" / "meta.json").read_text())
    g = grade_run(rd2, cfg, t)
    assert g["status"] == "pass", g["errors"][:1]


# --------------------------------------------------------------------------------------------- agents

def test_agent_timeout_leaves_children_running(tmp_path):
    """subprocess.run(timeout=...) kills only the direct child; the agent's background processes keep editing the
    workspace while it is graded (same for `docker run`, whose container survives the killed client)."""
    from agentexam.agents import run_agent
    rd = tmp_path / "rd"
    (rd / "workspace").mkdir(parents=True)
    (rd / "instruction.md").write_text("x")
    run_agent("cmd:bash -c '(sleep 2; echo late > late.txt) & sleep 30'", rd, timeout=1)
    time.sleep(3)
    assert not (rd / "workspace" / "late.txt").exists()
