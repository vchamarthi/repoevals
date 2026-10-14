"""The command line, end to end on the example repository."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from repoevals.cli import main

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}


@pytest.fixture
def repo(minisci_repo, monkeypatch, tmp_path):
    monkeypatch.chdir(minisci_repo)
    monkeypatch.setenv("REPOEVALS_RUNS_DIR", str(tmp_path / "cache"))
    return minisci_repo


def test_list_validate_check(repo, capsys, tmp_path):
    assert main(["list"]) == 0
    assert "fix_var_ddof" in capsys.readouterr().out
    assert main(["validate", "--source", str(repo)]) == 0
    assert main(["check", "fix_var_ddof", "--json", str(tmp_path / "c.json")]) == 0
    data = json.loads((tmp_path / "c.json").read_text())
    assert data["tasks"][0]["status"] == "ok" and "repoevals" in data["packages"]


def test_validate_fails_without_tasks(tmp_path, monkeypatch):
    (tmp_path / "repoevals.toml").write_text('[repo]\nid = "empty"\n')
    monkeypatch.chdir(tmp_path)
    assert main(["validate"]) == 1


def test_prepare_grade_show(repo, tmp_path, capsys):
    rd = tmp_path / "rd"
    assert main(["prepare", "fix_var_ddof", str(rd)]) == 0
    assert json.loads(capsys.readouterr().out)["task"] == "fix_var_ddof"
    assert main(["grade", str(rd)]) == 0
    assert '"status": "fail"' in capsys.readouterr().out
    assert main(["show", str(rd), "--diff", "--answer"]) == 0


def test_run_report_and_refusals(repo, tmp_path, capsys):
    assert main(["run", "--agent", "noop", "--out", str(repo / "runs")]) == 2  # inside the repository
    assert "inside the repository" in capsys.readouterr().out
    assert main(["run", "--agent", "noop", "--docker"]) == 2  # no [environment].image
    out = tmp_path / "runs"
    assert main(["run", "--agent", "noop", "--repeats", "1", "--out", str(out), "fix_var_ddof"]) == 0
    assert main(["report", str(out), "--fail-under", "0.5"]) == 1
    assert main(["report", str(out), "--compare", "arm"]) == 0
    assert (out / "report" / "summary.md").exists()


def test_export_harbor(repo, tmp_path):
    assert main(["export", "harbor", "fix_var_ddof", "--out", str(tmp_path / "h")]) == 0
    assert (tmp_path / "h" / "fix_var_ddof" / "tests" / "test.sh").exists()


def test_init_and_init_refuses_existing(tmp_path, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert '"mypkg/**"' in (tmp_path / "repoevals.toml").read_text()
    assert main(["init"]) == 1


def test_author_commands(repo, tmp_path, capsys):
    stats = repo / "minisci" / "stats.py"
    good = stats.read_text()
    stats.write_text(good.replace("(n - ddof)", "n"))
    subprocess.run(["git", "commit", "-qam", "break"], cwd=repo, check=True, env=GIT_ENV)
    stats.write_text(good)
    (repo / "tests" / "test_ddof_cli.py").write_text(
        "import minisci\n\n\ndef test_ddof():\n    assert minisci.var([1.0, 3.0], ddof=1) == 2.0\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=GIT_ENV)
    subprocess.run(["git", "commit", "-qm", "Fix ddof (#7)"], cwd=repo, check=True, env=GIT_ENV)
    assert main(["author", "mine", "--json", str(tmp_path / "m.json")]) == 0
    sha = json.loads((tmp_path / "m.json").read_text())[0]["sha"]
    assert main(["author", "fix", sha, "--id", "fix_cli", "--extract-tests"]) == 0
    with pytest.raises(ValueError):  # the instruction is still the template
        main(["author", "approve", "fix_cli"])
    (repo / ".agent-evals" / "tasks" / "fix_cli" / "instruction.md").write_text("var ignores ddof.\n")
    assert main(["author", "approve", "fix_cli"]) == 0
    assert main(["author", "pin"]) == 0
    assert main(["author", "mutants", "test_cli_mut", "minisci/distance.py", "--function", "chebyshev",
                 "--max", "5"]) == 0
    assert len(list((repo / ".agent-evals" / "tasks" / "test_cli_mut" / "mutants").glob("*.patch"))) == 5
    capsys.readouterr()
    assert main(["author", "playbook"]) == 0
    assert "Phase 1" in capsys.readouterr().out
    assert main(["author", "prompt", "--repo", "https://example.org/x"]) == 0
    assert "https://example.org/x" in capsys.readouterr().out
    assert main(["author", "install", "--codex", "--codex-home", str(tmp_path / "codex")]) == 0
    assert (repo / ".claude" / "skills" / "repoevals-task-author" / "SKILL.md").exists()


def test_doctor_with_test_target(repo, capsys):
    rc = main(["doctor", "tests/test_basic.py"])
    out = capsys.readouterr().out
    assert "test run on the current tree" in out and rc in (0, 1)
    assert Path(repo / "repoevals.toml").exists()


def test_task_selection_exclusions(repo):
    from repoevals.config import load_config
    from repoevals.task import discover
    cfg = load_config(repo / "repoevals.toml")
    allids = {t.id for t in discover(cfg.tasks_dir)}
    assert {t.id for t in discover(cfg.tasks_dir, ["!review_*"])} == {i for i in allids if not i.startswith("review_")}
    assert {t.id for t in discover(cfg.tasks_dir, ["family:review", "!review_clean_control"])} == {
        "review_seeded_softmax"}
    assert discover(cfg.tasks_dir, ["!family:fix", "fix_var_ddof"]) == []
