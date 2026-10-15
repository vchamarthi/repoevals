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


def test_report_keeps_distinct_cmd_agents_apart():
    from repoevals.report import config_key
    k1 = config_key({"agent": "cmd:agent-a {prompt_file}", "arm": "guidance:raw"})
    k2 = config_key({"agent": "cmd:agent-b {prompt_file}", "arm": "guidance:raw"})
    assert k1 != k2 and k1.startswith("cmd#") and k1.endswith("|-|guidance:raw")


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


def test_setup_failure_message_includes_log_tail(minisci_repo, tmp_path):
    with open(minisci_repo / "repoevals.toml", "a") as fh:
        fh.write('\n[environment]\nsetup = "echo building; echo error: BackendUnavailable no backend >&2; '
                 'for i in 1 2 3 4 5 6; do echo step $i; done; exit 2"\n')
    cfg = _cfg(minisci_repo)
    t = load_task(cfg.tasks_dir / "fix_var_ddof")
    with pytest.raises(RuntimeError, match="rc=2") as exc:
        prepare(cfg, t, tmp_path / "run", minisci_repo)
    assert "BackendUnavailable no backend" in str(exc.value)


FAKE_EXEC = """#!/usr/bin/env python3
import os, subprocess, sys
args, env, cwd, i = sys.argv[1:], dict(os.environ), None, 0
log = open(os.environ["FAKE_EXEC_LOG"], "a")
while args[i] in ("-w", "-e", "-i"):
    if args[i] == "-w":
        cwd = args[i + 1]; i += 2
    elif args[i] == "-e":
        k, v = args[i + 1].split("=", 1); env[k] = v; i += 2
    else:
        i += 1
log.write(" ".join(args[i:]) + "\\n")
sys.exit(subprocess.call(args[i:], cwd=cwd, env=env))
"""


@pytest.fixture
def fake_exec(tmp_path, monkeypatch):
    from repoevals import util
    script = tmp_path / "fakeexec"
    script.write_text(FAKE_EXEC)
    script.chmod(0o755)
    log = tmp_path / "exec.log"
    monkeypatch.setenv("FAKE_EXEC_LOG", str(log))
    monkeypatch.setattr(util, "EXEC_TEMPLATE", f"{script} -i {{env_flags}} -w {{cwd}}")
    return log


def test_run_shell_goes_through_exec_prefix(fake_exec, tmp_path):
    from repoevals.util import run_shell
    d = tmp_path / "w d"
    d.mkdir()
    p = run_shell("echo $FOO; pwd", cwd=d, env={"FOO": "a b"})
    assert p.ok and p.out.split("\n")[0] == "a b" and p.out.strip().endswith("w d")
    assert "bash -c echo $FOO; pwd" in fake_exec.read_text()


def test_exec_shims_forward_to_the_prefix(fake_exec, tmp_path):
    import subprocess

    from repoevals.util import write_exec_shims
    shims = write_exec_shims(tmp_path / "shims")
    out = subprocess.run([str(shims / "python"), "-c", "print(40 + 2)"], cwd=tmp_path, capture_output=True, text=True)
    assert out.stdout.strip() == "42"
    assert "python -c print(40 + 2)" in fake_exec.read_text()
    assert "python -m pytest" in (shims / "pytest").read_text()


def test_setup_and_grading_run_through_exec(minisci_repo, fake_exec, tmp_path):
    from repoevals import util
    with open(minisci_repo / "repoevals.toml", "a") as fh:
        fh.write('\n[environment]\nsetup = "echo built > .built"\n')
    cfg = _cfg(minisci_repo)
    util.EXEC_TEMPLATE = fake_exec.parent.joinpath("fakeexec").as_posix() + " -i {env_flags} -w {cwd}"
    t = load_task(cfg.tasks_dir / "fix_var_ddof")
    prepare(cfg, t, tmp_path / "run", minisci_repo)
    assert (tmp_path / "run" / "workspace" / ".built").exists()
    apply_oracle(tmp_path / "run", cfg)
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["status"] == "pass"
    log = fake_exec.read_text()
    assert "echo built" in log and "pytest" in log


def test_exec_config_is_loaded_and_reset(minisci_repo, tmp_path):
    from repoevals import util
    with open(minisci_repo / "repoevals.toml", "a") as fh:
        fh.write('\n[environment]\nexec = "docker exec -i {env_flags} -w {cwd} box"\n')
    _cfg(minisci_repo)
    assert util.EXEC_TEMPLATE.startswith("docker exec")
    (tmp_path / "other").mkdir()
    from repoevals.config import load_config
    load_config(root=tmp_path / "other")
    assert util.EXEC_TEMPLATE == ""


def _grade_with(minisci_repo, tmp_path, events, edit=None):
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    apply_oracle(tmp_path / "run", cfg)
    if edit:
        edit(ws)
    (tmp_path / "run" / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return grade_run(tmp_path / "run", cfg, t)


def test_local_git_history_commands_are_not_violations(minisci_repo, tmp_path):
    events = [{"kind": "command", "text": c, "rc": 0} for c in
              ("git log --oneline", "git show HEAD", "git -C . status", "git rev-list --all", "git cat-file -p HEAD")]
    g = _grade_with(minisci_repo, tmp_path, events)
    assert not any(f["check"] == "history_mining" and f["violation"] for f in g["integrity"]["integrity_findings"])
    from repoevals.integrity import HISTORY_RE
    for bad in ("git fetch upstream", "git -C /other/repo log", "git --git-dir=/x/.git log", "GIT_DIR=/x git log",
                "git remote add up https://x", "git clone https://x"):
        assert HISTORY_RE.search(bad), bad


def test_bytecode_in_protected_directories_is_not_a_change(minisci_repo, tmp_path):
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    cfg.protected_globs = ["tests/**"]
    (ws / "tests" / "__pycache__").mkdir(exist_ok=True)
    (ws / "tests" / "__pycache__" / "test_basic.cpython-312.pyc").write_bytes(b"x")
    (ws / ".gitignore").write_text("__pycache__/\n")
    from repoevals.grade import context
    ch = context(tmp_path / "run", cfg, t).tree().changed(context(tmp_path / "run", cfg, t).start_sha)
    assert not any("__pycache__" in p for p in ch)


def test_skip_in_new_test_file_is_not_tampering_but_in_existing_is(minisci_repo, tmp_path):
    def new_file(ws):
        (ws / "tests" / "test_new.py").write_text("import pytest\n\n\ndef test_x():\n    pytest.skip('n/a')\n")

    def existing(ws):
        t = ws / "tests" / "test_basic.py"
        t.write_text(t.read_text() + "\n\ndef test_y():\n    import pytest\n    pytest.skip('n/a')\n")
    ok = _grade_with(minisci_repo, tmp_path / "a", [], new_file)
    assert not any(f["check"] == "test_tampering" for f in ok["integrity"]["integrity_findings"])
    bad = _grade_with(minisci_repo, tmp_path / "b", [], existing)
    assert any(f["check"] == "test_tampering" for f in bad["integrity"]["integrity_findings"])


def test_regrade_integrity_only_updates_flags(minisci_repo, tmp_path, capsys):
    from repoevals.cli import main
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "runs" / "a")
    apply_oracle(tmp_path / "runs" / "a", cfg)
    g = grade_run(tmp_path / "runs" / "a", cfg, t)
    assert g["integrity"]["integrity_ok"] is True
    (ws / "sitecustomize.py").write_text("x = 1\n")  # something the checks flag
    assert main(["--config", str(minisci_repo / "repoevals.toml"), "grade", str(tmp_path / "runs"),
                 "--integrity-only"]) == 0
    after = json.loads((tmp_path / "runs" / "a" / "grade.json").read_text())
    assert after["integrity"]["integrity_ok"] is False and after["status"] == g["status"]
    assert "integrity_ok=False" in capsys.readouterr().out


def test_setup_is_retried_after_a_flaky_failure(minisci_repo, tmp_path):
    marker = tmp_path / "attempted"
    with open(minisci_repo / "repoevals.toml", "a") as fh:
        fh.write(f'\n[environment]\nsetup = "test -e {marker} || {{ : > {marker}; echo flaky >&2; exit 139; }}"\n')
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    log = (tmp_path / "run" / "setup.log").read_text()
    assert "setup attempt 1: rc=139" in log and "setup attempt 2: rc=0" in log


def test_errored_attempts_join_their_configuration():
    grades = [{"task": "t1", "agent": "a", "model": "m1", "arm": "raw", "status": "pass", "pass": True},
              {"task": "t2", "agent": "a", "model": None, "arm": "raw", "status": "error", "pass": None}]
    s = summarize(grades)
    assert list(s["arms"]) == ["a|m1|raw"] and s["arms"]["a|m1|raw"]["status"] == {"pass": 1, "error": 1}


# ------------------------------------------------------------------------- second code review (v0.4.0)

def test_background_processes_do_not_outlive_the_command(tmp_path):
    import time

    from repoevals.util import run_shell
    late = tmp_path / "late.txt"
    run_shell(f"(sleep 1; echo late > {late}) & echo started", cwd=tmp_path)
    time.sleep(2)
    assert not late.exists()


def test_empty_env_passthrough_means_none(minisci_repo, tmp_path):
    from repoevals.agents import DEFAULT_PASSTHROUGH, run_agent
    rd = tmp_path / "run"
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", rd)
    cmd = f"cmd:{sys.executable} -c \"import os; print(os.environ.get('ANTHROPIC_API_KEY', 'absent'))\""
    os.environ["ANTHROPIC_API_KEY"] = "sk-test"
    try:
        assert run_agent(cmd, rd, env_passthrough=[]).answer.strip() == "absent"
        assert "ANTHROPIC_API_KEY" in DEFAULT_PASSTHROUGH
        assert run_agent(cmd, rd, env_passthrough=None).answer.strip() == "sk-test"
    finally:
        del os.environ["ANTHROPIC_API_KEY"]


def test_arm_directories_are_injective():
    from repoevals.runner import arm_dirname
    a, b = arm_dirname("guidance:overlay=/tmp/a_b"), arm_dirname("guidance:overlay=/tmp/a/b")
    assert a != b
    assert arm_dirname("guidance:raw") == "guidance_raw"


def test_named_arm_install_cannot_escape_the_workspace(minisci_repo, tmp_path):
    (minisci_repo / "x.md").write_text("x\n")
    for dest in ("../escape.md", "/tmp/escape.md"):
        with open(minisci_repo / "repoevals.toml", "a") as fh:
            fh.write(f'\n[arms.bad{abs(hash(dest))}]\ninstall = {{ "{dest}" = "x.md" }}\n')
        name = f"bad{abs(hash(dest))}"
        with pytest.raises(ValueError, match="inside the workspace"):
            _prep(minisci_repo, "fix_var_ddof", tmp_path / f"run{name}", name)
    assert not (tmp_path / "escape.md").exists()


def test_author_ids_cannot_leave_the_tasks_directory(minisci_repo):
    from repoevals.authoring import task_path
    cfg = _cfg(minisci_repo)
    assert task_path(cfg, "fix_ok").parent == cfg.tasks_dir
    for bad in ("../../x", "/abs", "..", "a/b", ""):
        with pytest.raises(ValueError):
            task_path(cfg, bad)


def test_check_with_no_matching_tasks_fails(minisci_repo, monkeypatch, capsys):
    from repoevals.cli import main
    monkeypatch.chdir(minisci_repo)
    assert main(["check", "typoed-task"]) == 1
    assert "no tasks match" in capsys.readouterr().out


def test_review_patch_cannot_reintroduce_evaluation_files(minisci_repo, tmp_path):
    cfg = _cfg(minisci_repo)
    t = load_task(cfg.tasks_dir / "review_clean_control")
    patch = t.dir / "review.patch"
    original = patch.read_text()
    patch.write_text(original + "diff --git a/.agent-evals/tasks/x/answer.txt b/.agent-evals/tasks/x/answer.txt\n"
                                "new file mode 100644\n--- /dev/null\n+++ b/.agent-evals/tasks/x/answer.txt\n"
                                "@@ -0,0 +1 @@\n+secret\n")
    with pytest.raises(RuntimeError, match="evaluation paths"):
        prepare(cfg, t, tmp_path / "run", minisci_repo)


def test_gitignore_cannot_hide_files_from_grading(minisci_repo, tmp_path):
    cfg, t, ws = _prep(minisci_repo, "fix_var_ddof", tmp_path / "run")
    (ws / ".gitignore").write_text("hidden_module.py\n")
    (ws / "hidden_module.py").write_text("SECRET = 1\n")
    from repoevals.grade import context
    ctx = context(tmp_path / "run", cfg, t)
    assert "hidden_module.py" in ctx.tree().changed(ctx.start_sha)


def test_mutant_that_cannot_be_reverted_is_a_grader_error(minisci_repo, tmp_path):
    cfg, t, ws = _prep(minisci_repo, "test_chebyshev", tmp_path / "run")
    (ws / "tests" / "test_weak.py").write_text(
        "import minisci\nfrom pathlib import Path\n\n\n"
        "def test_x():\n    assert minisci.chebyshev([0.0], [0.0]) == 0.0\n")
    (tmp_path / "run" / "answer.txt").write_text('```json\n{"targets": ["tests/test_weak.py"]}\n```')
    sabotage = ws / "tests" / "test_weak.py"
    sabotage.write_text(sabotage.read_text() + (
        "\n\ndef test_sabotage():\n    p = Path(__file__).parent.parent / 'minisci' / 'distance.py'\n"
        "    p.write_text(p.read_text() + '\\n# touched by a test\\n')\n"))
    g = grade_run(tmp_path / "run", cfg, t)
    assert g["status"] in ("error", "fail") and (g["status"] == "error" or g["pass"] is False)


def test_report_deduplicates_symlinked_runs_and_survives_cycles(tmp_path):
    from repoevals.report import load_grades
    a = tmp_path / "runs" / "t1" / "r0"
    a.mkdir(parents=True)
    (a / "meta.json").write_text("{}")
    (a / "grade.json").write_text(json.dumps({"task": "t1", "status": "pass", "pass": True}))
    (tmp_path / "runs" / "alias").symlink_to(tmp_path / "runs" / "t1")
    (a / "loop").symlink_to(tmp_path / "runs")
    assert len(load_grades(tmp_path / "runs")) == 1


def test_one_run_without_cost_makes_the_total_unknown():
    grades = [{"task": "t1", "agent": "a", "model": "m", "arm": "raw", "status": "pass", "pass": True,
               "process": {"cost_usd": 1.25}},
              {"task": "t2", "agent": "a", "model": "m", "arm": "raw", "status": "pass", "pass": True, "process": {}}]
    assert summarize(grades)["arms"]["a|m|raw"]["cost_usd"] is None
    assert summarize(grades[:1])["arms"]["a|m|raw"]["cost_usd"] == 1.25


def test_review_mentioning_an_invalid_key_is_not_an_infra_error():
    acted = [{"kind": "command", "text": "ls", "rc": 0}]
    review = "Invalid API key handling returns the wrong status code in the new helper."
    assert classify(0, False, review, review, acted)[0] == "ok"
    notice = "Invalid API key · Please run /login"
    assert classify(0, False, notice, notice, acted)[0] == "infra_error"


def test_fail_under_fails_closed(minisci_repo, tmp_path, monkeypatch):
    from repoevals.cli import main
    monkeypatch.chdir(minisci_repo)
    out = tmp_path / "runs"
    err = out / "t" / "r0"
    err.mkdir(parents=True)
    (err / "meta.json").write_text("{}")
    (err / "grade.json").write_text(json.dumps({"task": "t", "agent": "a", "arm": "raw", "status": "error",
                                                "pass": None}))
    assert main(["report", str(out), "--fail-under", "0.1"]) == 1
    assert main(["report", str(tmp_path / "nothing"), "--fail-under", "0.1"]) == 1


def test_run_refuses_parallel_jobs_with_editable_install(minisci_repo, tmp_path):
    cfg = _cfg(minisci_repo)
    cfg.test.build = "pip install -e . --no-build-isolation -q"
    tasks = [load_task(cfg.tasks_dir / "fix_var_ddof")]
    with pytest.raises(RuntimeError, match="editable"):
        run_matrix(cfg, tasks, minisci_repo, tmp_path / "runs", RunOptions(agent="noop", jobs=4, arms=["guidance:raw"], repeats=1),
                   progress=lambda *_: None)
    run_matrix(cfg, tasks, minisci_repo, tmp_path / "runs", RunOptions(agent="noop", jobs=1, arms=["guidance:raw"],
                                                                         repeats=1), progress=lambda *_: None)


def test_run_stops_after_repeated_infrastructure_errors(minisci_repo, tmp_path):
    cfg = _cfg(minisci_repo)
    tasks = [load_task(cfg.tasks_dir / "fix_var_ddof")]
    opts = RunOptions(agent="cmd:definitely-not-an-agent-binary", arms=["guidance:raw"], repeats=6, retries=0)
    m = run_matrix(cfg, tasks, minisci_repo, tmp_path / "runs", opts, progress=lambda *_: None)
    assert "consecutive agent infrastructure errors" in m["stopped_reason"]
    assert m["counts"].get("not_run", 0) >= 1


def test_run_fails_fast_when_agent_cli_is_missing(minisci_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    cfg = _cfg(minisci_repo)
    tasks = [load_task(cfg.tasks_dir / "fix_var_ddof")]
    with pytest.raises(RuntimeError, match="not found on PATH"):
        run_matrix(cfg, tasks, minisci_repo, tmp_path / "runs", RunOptions(agent="claude-code", arms=["guidance:raw"], repeats=1),
                   progress=lambda *_: None)


def test_cloud_credentials_pass_through_only_when_provider_switch_is_set():
    from repoevals.agents import default_passthrough
    plain = default_passthrough({})
    assert "AWS_BEARER_TOKEN_BEDROCK" not in plain
    bedrock = default_passthrough({"CLAUDE_CODE_USE_BEDROCK": "1"})
    assert "AWS_BEARER_TOKEN_BEDROCK" in bedrock and "AWS_REGION" in bedrock and "ANTHROPIC_API_KEY" in bedrock
    assert "AWS_REGION" not in default_passthrough({"CLAUDE_CODE_USE_BEDROCK": "0"})


def test_api_retry_stream_is_an_infrastructure_error():
    out = '{"type":"system","subtype":"api_retry","attempt":8,"max_retries":10}'
    assert classify(1, False, out, "", [], traced=True)[0] == "infra_error"
    assert classify(1, False, "API Error: 400 does not support this model", "", [], traced=True)[0] == "infra_error"


def test_report_warns_at_the_ceiling_and_shows_cost_difference(tmp_path):
    def g(arm, task, i, cost):
        return {"task": task, "family": "fix", "grader": "hidden_tests", "arm": arm, "agent": "a", "model": "m",
                "status": "pass", "pass": True, "repeat": i, "process": {"cost_usd": cost}, "metrics": {}}
    grades = [g(arm, f"t{k}", i, c) for arm, c in (("guidance:raw", 2.0), ("guidance:none", 1.0))
              for k in range(3) for i in range(2)]
    s = summarize(grades, compare="arm")
    assert any("do not discriminate" in h["issue"] for h in s["task_health"])
    from repoevals.report import to_markdown
    md = to_markdown(s)
    assert "Δ cost $" in md and "-6.00" in md or "+6.00" in md
