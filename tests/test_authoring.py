"""Task authoring: mining history, scaffolding drafts, mutants, approval and the agent playbook."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from repoevals import authoring as A
from repoevals.check import check_all, validate_task
from repoevals.config import load_config
from repoevals.task import load_task

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, env=GIT_ENV, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo_with_fix(minisci_repo: Path) -> tuple[Path, str]:
    """minisci with a bug introduced, then fixed together with a new test (like a real merged PR)."""
    stats = minisci_repo / "minisci" / "stats.py"
    good = stats.read_text()
    stats.write_text(good.replace("return float(((x - m) ** 2).sum() / (n - ddof))",
                                  "return float(((x - m) ** 2).sum() / n)"))
    shutil.rmtree(minisci_repo / ".agent-evals" / "tasks")
    (minisci_repo / ".agent-evals" / "tasks").mkdir()
    _git(minisci_repo, "commit", "-qam", "Simplify variance")
    stats.write_text(good)
    (minisci_repo / "tests" / "test_var_ddof.py").write_text(
        "import numpy as np\nimport pytest\n\nimport minisci\n\n\n"
        "def test_var_ddof_one():\n    x = [1.0, 2.0, 3.0, 4.0]\n"
        "    assert minisci.var(x, ddof=1) == pytest.approx(np.var(x, ddof=1))\n")
    _git(minisci_repo, "add", "-A")
    _git(minisci_repo, "commit", "-qm", "Fix var ignoring ddof (#12)")
    return minisci_repo, _git(minisci_repo, "rev-parse", "HEAD")


def test_mine_finds_fix_commit(repo_with_fix):
    repo, sha = repo_with_fix
    cfg = load_config(repo / "repoevals.toml")
    cands = A.mine(cfg, repo)
    assert cands and cands[0]["sha"] == sha
    assert cands[0]["pr"] == 12
    assert cands[0]["lib_files"] == ["minisci/stats.py"]
    assert cands[0]["test_files"] == ["tests/test_var_ddof.py"]


def test_scaffold_fix_produces_a_task_that_passes_check(repo_with_fix, tmp_path):
    repo, sha = repo_with_fix
    cfg = load_config(repo / "repoevals.toml")
    d = A.scaffold_fix(cfg, repo, sha, task_id="fix_var_ddof")
    t = load_task(d)
    assert t.status == "draft" and t.grader["targets"] == ["tests/test_var_ddof.py"]
    assert (d / "hidden" / "tests" / "test_var_ddof.py").exists()
    errs = validate_task(t, cfg, repo)
    assert "instruction.md is still the draft template" in errs
    with pytest.raises(ValueError):
        A.approve(cfg, ["fix_var_ddof"])
    (d / "instruction.md").write_text("`minisci.var(x, ddof=1)` returns the population variance.\n")
    res = check_all([load_task(d)], cfg, repo, keep=tmp_path / "check")
    assert res[0]["status"] == "ok", res
    assert A.approve(cfg, ["fix_var_ddof"]) == ["fix_var_ddof"]
    assert load_task(d).status == "ready"


def test_mutants_generate_valid_patches_and_prune(minisci_repo):
    cfg = load_config(minisci_repo / "repoevals.toml")
    items = A.mutants(minisci_repo, "minisci/distance.py", function="chebyshev", max_n=15)
    assert len(items) >= 5
    for name, patch in items:
        check = subprocess.run(["git", "apply", "--check", "-"], cwd=minisci_repo, input=patch, text=True,
                               capture_output=True)
        assert check.returncode == 0, (name, check.stderr, patch)
    d = A.scaffold_mutation(cfg, minisci_repo, "test_cheb_gen", "minisci/distance.py", "chebyshev")
    A.write_mutants(d, items)
    shutil.copytree(minisci_repo / ".agent-evals/tasks/test_chebyshev/oracle_tests", d / "oracle_tests",
                    dirs_exist_ok=True)
    _git(minisci_repo, "add", "-A")
    _git(minisci_repo, "commit", "-qm", "add generated task")
    r = A.prune_mutants(cfg, minisci_repo, "test_cheb_gen")
    assert r["baseline_pass"] is True and r["kept"] >= 3
    remaining = sorted(p.stem for p in (d / "mutants").glob("*.patch"))
    assert len(remaining) == r["kept"]


def test_select_review_threads_filters_bots_replies_and_author():
    comments = [
        {"id": 1, "path": "a.py", "original_line": 10, "original_commit_id": "c1", "user": {"login": "rev"},
         "body": "this overflows"},
        {"id": 2, "path": "a.py", "original_line": 12, "original_commit_id": "c1", "user": {"login": "rev"},
         "body": "missing test", "in_reply_to_id": None},
        {"id": 3, "path": "a.py", "original_line": 10, "original_commit_id": "c1", "user": {"login": "rev"},
         "body": "reply", "in_reply_to_id": 1},
        {"id": 4, "path": "b.py", "original_line": 3, "original_commit_id": "c1",
         "user": {"login": "ci[bot]", "type": "Bot"}, "body": "lint"},
        {"id": 5, "path": "b.py", "original_line": 3, "original_commit_id": "c1", "user": {"login": "author"},
         "body": "note to self"},
        {"id": 6, "path": "c.py", "original_line": 1, "original_commit_id": "c0", "user": {"login": "rev"},
         "body": "old revision"},
    ]
    rev, threads = A.select_review_threads(comments, "author")
    assert rev == "c1"
    assert [(t["file"], t["line"]) for t in threads] == [("a.py", 10), ("a.py", 12)]


def test_playbook_prompt_and_install(minisci_repo, tmp_path):
    text = A.playbook_text()
    assert "Phase 3" in text and "repoevals author fix" in text
    prompt = A.prompt_text("https://github.com/org/proj", count=8)
    assert "https://github.com/org/proj" in prompt and "8" in prompt and A.PLAYBOOK_URL in prompt
    written = A.install_agent_files(minisci_repo, claude=True, codex_home=tmp_path / "codex")
    skill = minisci_repo / ".claude/skills/repoevals-task-author/SKILL.md"
    assert skill in written and skill.read_text().startswith("---\nname: repoevals-task-author")
    assert (tmp_path / "codex/prompts/repoevals-author.md").exists()


def test_run_skips_drafts(repo_with_fix, tmp_path, capsys, monkeypatch):
    from repoevals.cli import main
    repo, sha = repo_with_fix
    cfg = load_config(repo / "repoevals.toml")
    A.scaffold_fix(cfg, repo, sha, task_id="fix_var_ddof")
    monkeypatch.chdir(repo)
    rc = main(["run", "--agent", "noop", "--repeats", "1", "--out", str(tmp_path / "runs")])
    assert rc == 1 and "skipping 1 draft task" in capsys.readouterr().out
