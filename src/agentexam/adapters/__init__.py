"""Test adapters: run a repository's tests and map the outcome onto one vocabulary graders can rely on.

    passed       every selected test ran and passed
    failed       tests ran and at least one failed (this is what "a mutant was killed" means)
    build_error  the code did not build/import/collect, so no verdict on the tests themselves
    no_tests     the selection matched no tests
    timeout      the run exceeded its time limit
    error        anything else (tool missing, crashed runner)

Adapters are chosen by `[test].adapter` in agentexam.toml and can be overridden per task with `[grader].adapter`.
"""
from __future__ import annotations

import re
import shlex
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from ..config import TestConfig
from ..util import Proc, fmt, run_shell

STATUSES = ("passed", "failed", "build_error", "no_tests", "timeout", "error")


def quote_targets(targets: list[str]) -> str:
    """Targets can come from an agent's answer: always shell-quoted, never interpreted."""
    return " ".join(shlex.quote(str(t)) for t in targets)


@dataclass
class TestResult:
    status: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    rc: int = 0
    seconds: float = 0.0
    command: str = ""
    log: str = ""
    failing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def summary(self) -> dict:
        return {"status": self.status, "passed": self.passed, "failed": self.failed, "skipped": self.skipped,
                "rc": self.rc, "seconds": round(self.seconds, 2), "command": self.command,
                "failing": self.failing[:50], "log_tail": self.log[-4000:]}


class Adapter:
    name = "base"

    def __init__(self, cfg: TestConfig):
        self.cfg = cfg

    def build(self, ws: Path) -> Proc | None:
        if not self.cfg.build:
            return None
        return run_shell(self.cfg.build, cwd=ws, timeout=self.cfg.timeout_sec, env=self.cfg.env)

    def run(self, ws: Path, targets: list[str], timeout: float | None = None, build: bool = True) -> TestResult:
        if build:
            b = self.build(ws)
            if b is not None and not b.ok:
                return TestResult("timeout" if b.timed_out else "build_error", rc=b.rc, seconds=b.seconds,
                                  command=self.cfg.build, log=b.out)
        return self._run(ws, targets, timeout or self.cfg.timeout_sec)

    def _run(self, ws: Path, targets: list[str], timeout: float) -> TestResult:  # pragma: no cover
        raise NotImplementedError


class CommandAdapter(Adapter):
    """Any test runner: `[test].command` with {targets}; exit codes mapped through `[test].rc_map`."""
    name = "command"

    def _run(self, ws, targets, timeout):
        cmd = fmt(self.cfg.command or "{targets}", targets=quote_targets(targets))
        p = run_shell(cmd, cwd=ws, timeout=timeout, env=self.cfg.env)
        if p.timed_out:
            status = "timeout"
        else:
            status = self.cfg.rc_map.get(str(p.rc), "passed" if p.rc == 0 else "failed")
        return TestResult(status, rc=p.rc, seconds=p.seconds, command=cmd, log=p.out)


class PytestAdapter(Adapter):
    """pytest exit codes: 0 ok, 1 tests failed, 2 interrupted/collection error, 3 internal, 4 usage, 5 no tests."""
    name = "pytest"
    RC = {0: "passed", 1: "failed", 2: "build_error", 3: "error", 4: "error", 5: "no_tests"}

    def _run(self, ws, targets, timeout):
        with tempfile.TemporaryDirectory() as td:
            junit = Path(td) / "junit.xml"
            base = self.cfg.command or "python -m pytest -q -p no:cacheprovider {targets}"
            cmd = fmt(base, targets=quote_targets(targets)) + f" --junitxml={shlex.quote(str(junit))}"
            p = run_shell(cmd, cwd=ws, timeout=timeout, env=self.cfg.env)
            res = TestResult("timeout" if p.timed_out else self.RC.get(p.rc, "error"), rc=p.rc,
                             seconds=p.seconds, command=cmd, log=p.out)
            if junit.exists():
                try:
                    root = ET.parse(junit).getroot()
                    for case in root.iter("testcase"):
                        name = f"{case.get('classname', '')}::{case.get('name', '')}"
                        if case.find("failure") is not None or case.find("error") is not None:
                            res.failed += 1
                            res.failing.append(name)
                        elif case.find("skipped") is not None:
                            res.skipped += 1
                        else:
                            res.passed += 1
                except ET.ParseError:
                    pass
            # rc 0 is only a pass if pytest really ran and reported tests: a process that exits 0 early (os._exit,
            # sys.exit in a conftest) writes no JUnit report; a run where everything was skipped passed nothing
            if res.status == "passed" and not junit.exists():
                res.status = "error"
                res.log += "\nagentexam: pytest exited 0 without writing a JUnit report"
            elif res.status == "passed" and res.passed == 0:
                res.status = "no_tests"
            # collection errors are reported as errors with rc 1 or 2 depending on the plugin set
            if res.status == "failed" and res.passed == 0 and "error" in p.out.lower() and \
                    re.search(r"(ImportError|ModuleNotFoundError|SyntaxError|errors? during collection)", p.out):
                res.status = "build_error"
            return res


class BazelAdapter(Adapter):
    """bazel test exit codes: 0 ok, 1 build failed, 3 tests failed, 4 no tests, 8 interrupted."""
    name = "bazel"
    RC = {0: "passed", 1: "build_error", 3: "failed", 4: "no_tests", 8: "timeout"}

    def _run(self, ws, targets, timeout):
        base = self.cfg.command or "bazel test --nocache_test_results --test_output=errors {targets}"
        cmd = fmt(base, targets=quote_targets(targets))
        p = run_shell(cmd, cwd=ws, timeout=timeout, env=self.cfg.env)
        res = TestResult("timeout" if p.timed_out else self.RC.get(p.rc, "error"), rc=p.rc, seconds=p.seconds,
                         command=cmd, log=p.out)
        res.passed = len(re.findall(r"^//\S+\s+PASSED", p.out, re.M))
        res.failing = re.findall(r"^(//\S+)\s+FAILED", p.out, re.M)
        res.failed = len(res.failing)
        return res


class CTestAdapter(Adapter):
    """CTest: `[test].build` should configure and build; targets are -R regexes (joined with |)."""
    name = "ctest"

    def _run(self, ws, targets, timeout):
        sel = f"-R {shlex.quote('|'.join(targets))}" if targets else ""
        base = self.cfg.command or "ctest --test-dir build --output-on-failure {targets}"
        cmd = fmt(base, targets=sel)
        p = run_shell(cmd, cwd=ws, timeout=timeout, env=self.cfg.env)
        m = re.search(r"(\d+)% tests passed, (\d+) tests failed out of (\d+)", p.out)
        if p.timed_out:
            status = "timeout"
        elif "No tests were found" in p.out:
            status = "no_tests"
        elif p.rc == 0:
            status = "passed"
        elif m:
            status = "failed"
        else:
            status = "build_error"
        res = TestResult(status, rc=p.rc, seconds=p.seconds, command=cmd, log=p.out)
        if m:
            res.failed, total = int(m.group(2)), int(m.group(3))
            res.passed = total - res.failed
        return res


ADAPTERS = {a.name: a for a in (CommandAdapter, PytestAdapter, BazelAdapter, CTestAdapter)}


def get_adapter(cfg: TestConfig, override: dict | None = None) -> Adapter:
    """Adapter for the repo config, optionally overridden by a task's [grader] keys (adapter, build, command, env)."""
    override = override or {}
    if any(k in override for k in ("adapter", "build", "test_command", "test_env")):
        cfg = TestConfig(adapter=override.get("adapter", cfg.adapter), build=override.get("build", cfg.build),
                         command=override.get("test_command", cfg.command), timeout_sec=cfg.timeout_sec,
                         env={**cfg.env, **override.get("test_env", {})}, rc_map=cfg.rc_map)
    if cfg.adapter not in ADAPTERS:
        raise KeyError(f"unknown test adapter {cfg.adapter!r}; known: {sorted(ADAPTERS)}")
    return ADAPTERS[cfg.adapter](cfg)
