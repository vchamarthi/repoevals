"""End-to-end: the example repository's tasks, graded with the null agent, the oracle, and scripted agents."""
import json
from pathlib import Path

from repoevals.check import check_all, validate_task
from repoevals.config import load_config
from repoevals.grade import apply_oracle, grade_run
from repoevals.report import write_report
from repoevals.task import discover, load_task
from repoevals.util import git
from repoevals.workspace import prepare


def _cfg(repo: Path):
    return load_config(repo / "repoevals.toml")


def test_validate_examples(minisci_repo):
    cfg = _cfg(minisci_repo)
    tasks = discover(cfg.tasks_dir)
    assert len(tasks) == 6
    for t in tasks:
        errs = [e for e in validate_task(t, cfg, minisci_repo) if not e.startswith("warning:")]
        assert errs == [], (t.id, errs)


def test_self_check_all_tasks(minisci_repo, tmp_path):
    cfg = _cfg(minisci_repo)
    res = check_all(discover(cfg.tasks_dir), cfg, minisci_repo, keep=tmp_path / "check")
    bad = [r for r in res if r["status"] != "ok"]
    assert not bad, json.dumps(bad, indent=1)
    by = {r["task"]: r for r in res}
    assert by["test_chebyshev"]["oracle_kill_rate"] == 1.0
    assert by["review_seeded_softmax"]["spam"]["status"] == "fail"


def test_workspace_hides_history_and_tasks(minisci_repo, tmp_path):
    cfg = _cfg(minisci_repo)
    t = load_task(cfg.tasks_dir / "fix_var_ddof")
    meta = prepare(cfg, t, tmp_path / "run", minisci_repo)
    ws = tmp_path / "run" / "workspace"
    assert not (ws / ".agent-evals").exists()
    assert not (ws / "repoevals.toml").exists()
    assert git(ws, "rev-list", "--all", "--count").out.strip() == "1"
    assert "/ n)" in (ws / "minisci" / "stats.py").read_text()  # setup.patch applied: bug present
    assert meta["start_sha"]


def test_guidance_none_arm_removes_agents_md(minisci_repo, tmp_path):
    cfg = _cfg(minisci_repo)
    t = load_task(cfg.tasks_dir / "fix_var_ddof")
    prepare(cfg, t, tmp_path / "raw", minisci_repo, "guidance:raw")
    prepare(cfg, t, tmp_path / "none", minisci_repo, "guidance:none")
    assert (tmp_path / "raw" / "workspace" / "AGENTS.md").exists()
    assert not (tmp_path / "none" / "workspace" / "AGENTS.md").exists()


def test_test_tampering_is_flagged_and_restored(minisci_repo, tmp_path):
    """An agent that 'fixes' the bug by weakening the visible regression test still fails the hidden tests,
    and the tampering is reported by the integrity checks."""
    cfg = _cfg(minisci_repo)
    t = load_task(cfg.tasks_dir / "fix_var_ddof")
    rd = tmp_path / "run"
    prepare(cfg, t, rd, minisci_repo)
    ws = rd / "workspace"
    test = ws / "tests" / "test_basic.py"
    test.write_text(test.read_text().replace("def test_mean():", "@pytest.mark.skip\ndef test_mean():"))
    (rd / "answer.txt").write_text("done")
    g = grade_run(rd, cfg, t)
    assert g["pass"] is False
    assert g["integrity"]["integrity_ok"] is False
    assert any(f["check"] == "test_tampering" for f in g["integrity"]["integrity_findings"])
    assert "tests/test_basic.py" in g["details"]["reset_before_grading"]
    # grading restores the agent's tree exactly
    assert "pytest.mark.skip" in test.read_text()


def test_history_mining_detected_from_trace(minisci_repo, tmp_path):
    cfg = _cfg(minisci_repo)
    t = load_task(cfg.tasks_dir / "fix_var_ddof")
    rd = tmp_path / "run"
    prepare(cfg, t, rd, minisci_repo)
    apply_oracle(rd, cfg)
    (rd / "events.jsonl").write_text(json.dumps({"kind": "command", "text": "git log --all -p", "rc": 0}) + "\n")
    g = grade_run(rd, cfg, t)
    assert g["pass"] is True  # not gated by default...
    assert not g["integrity"]["integrity_ok"]  # ...but reported


def test_review_spam_fails_and_precision_reported(minisci_repo, tmp_path):
    cfg = _cfg(minisci_repo)
    t = load_task(cfg.tasks_dir / "review_seeded_softmax")
    rd = tmp_path / "run"
    prepare(cfg, t, rd, minisci_repo)
    findings = [{"file": "minisci/stats.py", "line": i, "severity": "minor", "rule": "r", "message": "overflow axis"}
                for i in range(30, 50)]
    (rd / "answer.txt").write_text("```json\n" + json.dumps(findings) + "\n```")
    g = grade_run(rd, cfg, t)
    assert g["pass"] is False
    assert g["metrics"]["precision"] < 0.5


def test_mutation_weak_tests_fail(minisci_repo, tmp_path):
    cfg = _cfg(minisci_repo)
    t = load_task(cfg.tasks_dir / "test_chebyshev")
    rd = tmp_path / "run"
    prepare(cfg, t, rd, minisci_repo)
    (rd / "workspace" / "tests" / "test_cheb_weak.py").write_text(
        "import minisci\n\ndef test_runs():\n    assert minisci.chebyshev([1.0], [1.0]) == 0.0\n")
    (rd / "answer.txt").write_text('```json\n{"targets": ["tests/test_cheb_weak.py"]}\n```')
    g = grade_run(rd, cfg, t)
    assert g["metrics"]["baseline_pass"] is True
    assert g["metrics"]["kill_rate"] < 0.8
    assert g["pass"] is False


def test_report_aggregates_runs(minisci_repo, tmp_path):
    cfg = _cfg(minisci_repo)
    t = load_task(cfg.tasks_dir / "fix_var_ddof")
    runs = tmp_path / "runs"
    for arm in ("guidance:raw", "guidance:none"):
        for rep in range(2):
            rd = runs / t.id / arm.replace(":", "_") / f"r{rep}"
            prepare(cfg, t, rd, minisci_repo, arm)
            if arm == "guidance:raw":
                apply_oracle(rd, cfg)
            meta = json.loads((rd / "meta.json").read_text())
            meta.update(agent="scripted", repeat=rep)
            (rd / "meta.json").write_text(json.dumps(meta))
            grade_run(rd, cfg, t)
    s = write_report(runs, tmp_path / "report")
    arms = s["arms"]
    assert arms["scripted|-|guidance:raw"]["mean_pass_rate"] == 1.0
    assert arms["scripted|-|guidance:none"]["mean_pass_rate"] == 0.0
    assert (tmp_path / "report" / "summary.md").exists()
    assert len((tmp_path / "report" / "results.jsonl").read_text().splitlines()) == 4
