"""Guidance suggestions: evidence mining, drafts, the overlay arm and A/B verification."""
import json
import sys
from pathlib import Path

import pytest

from repoevals import suggest as S
from repoevals.config import load_config
from repoevals.task import load_task
from repoevals.workspace import prepare


def _run(root: Path, name: str, task: str, arm: str, passed: bool, events: list[dict], agent="claude-code",
         violations=(), guidance=True) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"task": task, "arm": arm, "agent": agent,
                                             "guidance": {"AGENTS.md": "x"} if guidance else {}}))
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    findings = [{"check": v, "violation": True, "evidence": "x"} for v in violations]
    (d / "grade.json").write_text(json.dumps({
        "task": task, "arm": arm, "agent": agent, "status": "pass" if passed else "fail", "pass": passed,
        "integrity": {"integrity_ok": not findings, "integrity_findings": findings}}))


@pytest.fixture
def runs(tmp_path) -> Path:
    root = tmp_path / "runs"
    no_device = {"kind": "command", "text": "cd ws && python -m pytest -q tests/test_a.py::t", "rc": 1,
                 "output": "dpctl._sycl_device.SyclDeviceCreationError: Default device is unavailable."}
    explore = {"kind": "read", "path": "/x/workspace/setup.py"}
    edit = {"kind": "edit", "path": "/x/workspace/pkg/a.py"}
    _run(root, "r1", "t1", "guidance:raw", False, [explore, no_device, edit], violations=["test_tampering"])
    _run(root, "r2", "t1", "guidance:raw", False, [explore, no_device, edit])
    _run(root, "r3", "t1", "guidance:none", True, [explore, edit], guidance=False)
    _run(root, "r4", "t2", "guidance:raw", True, [edit])
    _run(root, "r5", "t2", "guidance:none", True, [{"kind": "command", "text": "foo --bar", "rc": 127,
                                                     "output": "bash: foo: command not found"}], guidance=False)
    return root


def test_norm_command():
    assert S._norm_command("cd ws && FOO=1 python -m pytest -q tests/a.py::t") == "python -m pytest"
    assert S._norm_command("make -j4 all") == "make"
    assert S._norm_command("git status") == "git status"


def test_analyze_finds_recurring_problems(minisci_repo, runs):
    cfg = load_config(minisci_repo / "repoevals.toml")
    ev = S.analyze(runs, cfg)
    assert ev["runs"] == 5
    assert ev["failing_commands"][0]["item"] == "python -m pytest" and ev["failing_commands"][0]["runs"] == 2
    assert any("SYCL device" in e["item"] for e in ev["environment"])
    assert any(x["item"] == "setup.py" and x["runs"] == 3 for x in ev["exploration"])
    assert ev["arm_regressions"] == [{"task": "t1", "arm": "guidance:raw", "pass_rate": 0.0, "without": 1.0,
                                      "runs": 3}]
    assert ev["rule_violations"][0]["check"] == "test_tampering"
    assert any("CLAUDE.md" in c for c in ev["coverage"])  # AGENTS.md exists, CLAUDE.md does not
    # a problem seen in a single run is below the default threshold
    assert not any("foo" in e["item"] for e in ev["environment"])
    assert any("foo" in e["item"] for e in S.analyze(runs, cfg, min_runs=1)["environment"])


def test_suggestions_and_markdown(minisci_repo, runs):
    cfg = load_config(minisci_repo / "repoevals.toml")
    ev = S.analyze(runs, cfg)
    sugg = S.suggestions(ev)
    text = "\n".join(sugg)
    assert "python -m pytest" in text and "t1" in text and "test_tampering" in text and "setup.py" in text
    md = S.evidence_markdown(ev, sugg)
    assert "## Failing commands" in md and "## Tasks worse with guidance" in md


def test_deterministic_draft_is_idempotent(minisci_repo, runs, tmp_path):
    cfg = load_config(minisci_repo / "repoevals.toml")
    r = S.suggest(runs, cfg, tmp_path / "out")
    draft = Path(r["draft_dir"])
    agents = (draft / "AGENTS.md").read_text()
    assert agents.startswith("# minisci agent guide") and S.NOTES_HEADER in agents and "TODO:" in agents
    assert (draft / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    assert (tmp_path / "out" / "evidence.md").exists() and json.loads((tmp_path / "out" / "evidence.json")
                                                                       .read_text())["suggestions"]
    (minisci_repo / "AGENTS.md").write_text(agents)  # a repository that already adopted a notes section
    S.deterministic_draft(cfg, ["x"], tmp_path / "again")
    assert (tmp_path / "again" / "AGENTS.md").read_text().count(S.NOTES_HEADER) == 1


def test_llm_draft_with_command_agent(minisci_repo, runs, tmp_path):
    fake = tmp_path / "fake_agent.py"
    fake.write_text(
        "import json, sys\nprompt = sys.stdin.read()\nassert 'Default device is unavailable' in prompt\n"
        "assert '# minisci agent guide' in prompt\n"
        "print('```json\\n' + json.dumps({'AGENTS.md': 'Run tests with ONEAPI_DEVICE_SELECTOR=opencl:cpu.\\n',"
        " 'CLAUDE.md': '@AGENTS.md\\n', '../evil.md': 'x', 'minisci/stats.py': 'x = 1'}) + '\\n```')\n")
    cfg = load_config(minisci_repo / "repoevals.toml")
    r = S.suggest(runs, cfg, tmp_path / "out", draft_with=f"cmd:{sys.executable} {fake}")
    assert sorted(r["draft_files"]) == ["AGENTS.md", "CLAUDE.md"]  # traversal and non-guidance paths dropped
    assert not (tmp_path / "evil.md").exists()
    assert "ONEAPI_DEVICE_SELECTOR" in (Path(r["draft_dir"]) / "AGENTS.md").read_text()


def test_llm_draft_without_json_fails(minisci_repo, runs, tmp_path):
    cfg = load_config(minisci_repo / "repoevals.toml")
    with pytest.raises(RuntimeError, match="no JSON"):
        S.suggest(runs, cfg, tmp_path / "out", draft_with=f"cmd:{sys.executable} -c print('sorry')")


def test_overlay_arm_adds_files_and_keeps_others(minisci_repo, tmp_path):
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "CLAUDE.md").write_text("@AGENTS.md\n")
    cfg = load_config(minisci_repo / "repoevals.toml")
    t = load_task(cfg.tasks_dir / "fix_var_ddof")
    prepare(cfg, t, tmp_path / "run", minisci_repo, f"guidance:overlay={draft}")
    ws = tmp_path / "run" / "workspace"
    assert (ws / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    assert (ws / "AGENTS.md").exists()  # existing guidance kept


def test_guidance_cli_suggest_and_verify(minisci_repo, runs, tmp_path, monkeypatch, capsys):
    from repoevals.cli import main
    monkeypatch.chdir(minisci_repo)
    assert main(["guidance", "suggest", str(runs), "--out", str(tmp_path / "g")]) == 0
    out = capsys.readouterr().out
    assert "suggestion(s)" in out and "guidance verify" in out
    assert main(["guidance", "verify", str(tmp_path / "g" / "draft"), "fix_var_ddof", "--agent", "noop",
                 "--repeats", "1", "--out", str(tmp_path / "verify")]) == 0
    out = capsys.readouterr().out
    assert "no clear difference" in out
    assert (tmp_path / "verify" / "verify.md").exists()
    assert main(["guidance", "lint"]) == 0


def test_verify_excludes_evidence_tasks(minisci_repo, tmp_path, monkeypatch, capsys):
    from repoevals.cli import main
    monkeypatch.chdir(minisci_repo)
    evidence = tmp_path / "evidence"
    _run(evidence, "r1", "fix_var_ddof", "guidance:raw", True, [])
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "CLAUDE.md").write_text("@AGENTS.md\n")
    assert main(["guidance", "verify", str(draft), "fix_var_ddof", "--agent", "noop", "--repeats", "1",
                 "--exclude-tasks-in", str(evidence), "--out", str(tmp_path / "v")]) == 1
    assert "no tasks to run" in capsys.readouterr().out


def test_suggestions_are_deduplicated(minisci_repo, runs):
    cfg = load_config(minisci_repo / "repoevals.toml")
    sugg = S.suggestions(S.analyze(runs, cfg))
    assert len(sugg) == len(set(sugg))
    assert sum("CLAUDE.md" in s for s in sugg) == 1


def test_draft_prompt_forbids_task_specific_hints():
    assert "never mention those tasks" in S.DRAFT_PROMPT
