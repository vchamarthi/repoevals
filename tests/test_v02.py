"""Run-matrix robustness, agent error handling, arms, reports, and fixes for the 0.2 code review."""
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from repoevals.agents import classify
from repoevals.config import load_config
from repoevals.grade import apply_oracle, grade_run
from repoevals.graders.tests import artifact_ok
from repoevals.report import summarize, write_report
from repoevals.runner import RunOptions, check_out_dir, run_matrix
from repoevals.task import load_task
from repoevals.util import run_shell
from repoevals.workspace import prepare


def _cfg(repo: Path):
    return load_config(repo / "repoevals.toml")


def _prep(repo, task_id, rd, arm="guidance:raw"):
    cfg = _cfg(repo)
    t = load_task(cfg.tasks_dir / task_id)
    prepare(cfg, t, rd, repo, arm)
    return cfg, t, rd / "workspace"


# ------------------------------------------------------------------------------------------- security regressions

def test_shadowing_pytest_module_cannot_fake_a_pass(minisci_repo, tmp_path):
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    (ws / "pytest.py").write_text(
        "import sys\nfor a in sys.argv:\n    if a.startswith('--junitxml='):\n"
        "        open(a.split('=',1)[1],'w').write('<testsuite><testcase classname=\"x\" name=\"y\"/></testsuite>')\n"
        "sys.exit(0)\n")
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["pass"] is False
    assert "pytest.py" in g["details"]["reset_before_grading"]


def test_task_ids_must_be_plain_names(tmp_path):
    d = tmp_path / "evil"
    d.mkdir()
    (d / "task.toml").write_text('version = "1.0"\n[metadata]\nid = "../../outside"\n[grader]\ntype = "command"\n')
    with pytest.raises(ValueError, match="invalid task id"):
        load_task(d)


def test_numeric_candidate_cannot_call_the_reference(minisci_repo, tmp_path):
    cfg, t, ws = _prep(minisci_repo, "numeric_logsumexp", tmp_path / "run")
    init = ws / "minisci" / "__init__.py"
    init.write_text(init.read_text() + (
        "\nimport sys as _s\n"
        "def _cheat(x):\n"
        "    for m in list(_s.modules.values()):\n"
        "        ref = getattr(m, 'reference', None)\n"
        "        if callable(ref) and hasattr(m, 'CASES'):\n"
        "            return float(ref({'x': x}))\n"
        "    return float('nan')\n"
        "logsumexp = _cheat\n"))
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["pass"] is False


def test_grader_commands_do_not_see_credentials(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    p = run_shell("env")
    assert "secret-value" not in p.out and "sk-secret" not in p.out


def test_content_free_review_findings_earn_nothing(minisci_repo, tmp_path):
    cfg, t, ws = _prep(minisci_repo, "review_seeded_softmax", tmp_path / "run")
    findings = [{"file": "minisci/stats.py", "line": n} for n in (35, 36, 37, 43, 45)]
    (tmp_path / "run" / "answer.txt").write_text("```json\n" + json.dumps(findings) + "\n```")
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["metrics"]["recall"] == 0.0 and g["pass"] is False


def test_artifact_checks_structure_not_magic(tmp_path):
    fake = tmp_path / "libfake.so"
    fake.write_bytes(b"\x7fELF" + b"\0" * 2000)
    assert not artifact_ok(fake, "elf")
    hdr = bytearray(64)
    hdr[:4] = b"\x7fELF"
    hdr[4], hdr[5] = 2, 1
    hdr[16:18] = struct.pack("<H", 3)
    real = tmp_path / "libreal.so"
    real.write_bytes(bytes(hdr) + b"\0" * 2000)
    assert artifact_ok(real, "elf")
    tiny = tmp_path / "tiny.so"
    tiny.write_bytes(bytes(hdr))
    assert not artifact_ok(tiny, "elf")


def test_regrading_uses_the_task_snapshot(minisci_repo, tmp_path):
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    apply_oracle(tmp_path / "run", cfg)
    first = grade_run(tmp_path / "run", cfg)
    (t.dir / "hidden" / "tests" / "test_var_hidden.py").write_text("def test_x():\n    assert False\n")
    second = grade_run(tmp_path / "run", cfg)
    assert first["status"] == second["status"] == "pass"
    assert (tmp_path / "run" / "agent.diff").read_text().startswith("diff --git")


# ------------------------------------------------------------------------------------------- agents and runner

def test_classify_agent_failures():
    assert classify(127, False, "", "", [])[0] == "infra_error"
    assert classify(1, False, "Error: 429 rate limit exceeded", "", [])[0] == "infra_error"
    assert classify(0, True, "", "", [])[0] == "timeout"
    assert classify(1, False, "boom", "", [{"kind": "command", "text": "ls"}])[0] == "ok"
    assert classify(0, False, "", "", [], traced=False)[0] == "ok"


def test_out_dir_inside_repository_is_refused(minisci_repo):
    cfg = _cfg(minisci_repo)
    assert check_out_dir(minisci_repo / "runs", cfg, minisci_repo)
    assert not check_out_dir(minisci_repo.parent / "elsewhere", cfg, minisci_repo)


def test_run_matrix_errors_skips_and_resume(minisci_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("REPOEVALS_DEVICES", "cpu")
    cfg = _cfg(minisci_repo)
    gpu = cfg.tasks_dir / "fix_var_ddof_gpu"
    import shutil
    shutil.copytree(cfg.tasks_dir / "fix_var_ddof", gpu)
    toml = (gpu / "task.toml").read_text() + '\n[environment]\nrequires = { devices = ["gpu"] }\n'
    (gpu / "task.toml").write_text(toml)
    tasks = [load_task(cfg.tasks_dir / "fix_var_ddof"), load_task(gpu)]
    out = tmp_path / "runs"
    missing = RunOptions(agent="cmd:definitely-not-an-agent-binary", arms=["guidance:raw"], repeats=1, retries=0)
    m = run_matrix(cfg, tasks, minisci_repo, out, missing, progress=lambda *_: None)
    assert m["counts"] == {"error": 1, "skipped": 1}
    g = json.loads((out / "fix_var_ddof" / "guidance_raw" / "r0" / "grade.json").read_text())
    assert g["status"] == "error" and "infrastructure" in g["errors"][0]
    noop = RunOptions(agent="noop", arms=["guidance:raw"], repeats=1, resume=True)
    m = run_matrix(cfg, tasks, minisci_repo, out, noop, progress=lambda *_: None)
    assert m["counts"] == {"fail": 1, "skipped": 1}  # the errored attempt was redone, the skip kept
    run_json = json.loads((out / "run.json").read_text())
    assert run_json["agent"] == "noop" and run_json["source_sha"]
    assert "repoevals" in run_json["packages"] and "pytest" in run_json["packages"]


def test_guidance_file_arm_installs_for_every_agent(minisci_repo, tmp_path):
    guide = tmp_path / "guide.md"
    guide.write_text("Use NumPy semantics.\n")
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run", f"guidance:file={guide}")
    assert (ws / "AGENTS.md").read_text() == (ws / "CLAUDE.md").read_text() == "Use NumPy semantics.\n"


def test_named_arm_from_config(minisci_repo, tmp_path):
    (minisci_repo / "exp").mkdir()
    (minisci_repo / "exp" / "terse.md").write_text("Be brief.\n")
    with open(minisci_repo / "repoevals.toml", "a") as fh:
        fh.write('\n[arms.terse]\ninstall = { "AGENTS.md" = "exp/terse.md" }\n')
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run", "terse")
    assert (ws / "AGENTS.md").read_text() == "Be brief.\n"
    meta = json.loads((tmp_path / "run" / "meta.json").read_text())
    assert list(meta["guidance"]) == ["AGENTS.md"] and meta["task_hash"]


# ------------------------------------------------------------------------------------------- reports and CLI

def test_report_keeps_score_only_configurations_and_unreadable_grades(tmp_path):
    a = tmp_path / "runs" / "t1" / "r0"
    a.mkdir(parents=True)
    (a / "meta.json").write_text("{}")
    (a / "grade.json").write_text(json.dumps({"task": "t1", "agent": "x", "arm": "raw", "status": "scored",
                                              "pass": None, "metrics": {"f1": 0.5}, "process": {}}))
    b = tmp_path / "runs" / "t2" / "r0"
    b.mkdir(parents=True)
    (b / "meta.json").write_text(json.dumps({"task": "t2", "agent": "x", "arm": "raw"}))
    (b / "grade.json").write_text("{not json")
    s = write_report(tmp_path / "runs", tmp_path / "report")
    arm = s["arms"]["x|-|raw"]
    assert arm["n_runs"] == 2 and arm["scores"] == {"t1:f1": 0.5}
    assert arm["status"] == {"scored": 1, "error": 1} and arm["cost_usd"] is None
    assert any(h["task"] == "t2" for h in s["task_health"])


def test_summarize_by_family():
    grades = [{"task": f"t{i}", "family": "fix" if i < 2 else "review", "agent": "a", "arm": "raw",
               "status": "pass" if i % 2 else "fail", "pass": bool(i % 2)} for i in range(4)]
    s = summarize(grades)
    assert set(s["arms"]["a|-|raw"]["families"]) == {"fix", "review"}


def test_cli_show_doctor_and_pin(minisci_repo, tmp_path, capsys, monkeypatch):
    from repoevals.cli import main
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    apply_oracle(tmp_path / "run", cfg)
    grade_run(tmp_path / "run", cfg, t)
    assert main(["show", str(tmp_path / "run")]) == 0
    assert "strict_pass" in capsys.readouterr().out
    monkeypatch.chdir(minisci_repo)
    monkeypatch.setenv("REPOEVALS_RUNS_DIR", str(tmp_path / "cache"))
    main(["doctor"])
    assert "task(s)" in capsys.readouterr().out
    assert main(["author", "pin", "fix_var_ddof"]) == 0
    assert len(load_task(cfg.tasks_dir / "fix_var_ddof").base) == 40


def test_init_with_preset(tmp_path, monkeypatch):
    from repoevals.cli import main
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--preset", "dpnp"]) == 0
    assert 'id = "dpnp"' in (tmp_path / "repoevals.toml").read_text()
    assert (tmp_path / ".agent-evals" / "tasks").is_dir()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_large_output_is_bounded(tmp_path):
    from repoevals.util import MAX_OUTPUT, run
    p = run([sys.executable, "-c", f"import sys; sys.stdout.write('x' * {MAX_OUTPUT + 1000})"])
    assert p.ok and len(p.out) < MAX_OUTPUT + 200 and "omitted" in p.out
    assert os.environ.get("PATH")


def test_workspace_disables_background_git_maintenance(minisci_repo, tmp_path):
    from repoevals.util import git
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    assert git(ws, "config", "gc.auto").out.strip() == "0"


def test_runner_shadowing_is_an_integrity_violation(minisci_repo, tmp_path):
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    (ws / "sitecustomize.py").write_text("import os\n")
    g = grade_run(tmp_path / "run", cfg, t)
    assert any(f["check"] == "runner_shadowing" for f in g["integrity"]["integrity_findings"])


def test_report_names_cmd_agents_briefly():
    from repoevals.report import config_key
    assert config_key({"agent": "cmd:python /long/path/agent.py x {prompt_file}", "arm": "guidance:raw",
                       "label": "oracle"}) == "oracle|cmd|-|guidance:raw"


def test_report_compare_restricts_pairs():
    grades = [{"task": f"t{i}", "agent": ag, "arm": arm, "status": "pass", "pass": True}
              for i in range(3) for ag in ("a", "b") for arm in ("guidance:raw", "guidance:none")]
    s = summarize(grades, compare="arm")
    assert len(s["comparisons"]) == 2
    assert all(c["a"].split("|")[0] == c["b"].split("|")[0] for c in s["comparisons"])


def test_setup_sees_source_and_task_dir(minisci_repo, tmp_path):
    with open(minisci_repo / "repoevals.toml", "a") as fh:
        fh.write('\n[environment]\nsetup = "echo $REPOEVALS_TASK_DIR > .setup-env && test -d $REPOEVALS_SOURCE/.git"\n')
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    assert (ws / ".setup-env").read_text().strip() == str(t.dir)


def test_importorskip_in_new_tests_is_not_tampering(tmp_path):
    r = _repo_with_test_v02(tmp_path, 'np = pytest.importorskip("numpy")')
    assert r["integrity_ok"] is True


def _repo_with_test_v02(tmp_path, added_line):
    from repoevals.integrity import check
    from repoevals.util import git
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n")
    for c in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-qm", "i"]):
        subprocess.run(c, cwd=ws, check=True, env=env)
    base = git(ws, "rev-parse", "HEAD").out.strip()
    (ws / "tests" / "test_a.py").write_text("import pytest\n" + added_line + "\n\ndef test_a():\n    assert 1\n")
    return check(ws, base, [], {"tests/test_a.py": "M"}, ["tests/**"], [], [])


def test_usage_limit_mid_run_is_infra_error():
    acted = [{"kind": "command", "text": "ls", "rc": 0}]
    msg = "You've hit your session limit · resets 11:10am (America/Chicago)"
    assert classify(0, False, msg, msg, acted)[0] == "infra_error"
    long_review = "The change has a session limit option that is ignored. " + "x" * 700
    assert classify(0, False, long_review, long_review, acted)[0] == "ok"
