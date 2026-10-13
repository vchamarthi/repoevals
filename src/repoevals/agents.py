"""Agents under test. An agent turns (workspace, instruction) into an edited workspace + final answer + trace.

Built-in agents:
  claude-code   Claude Code CLI, headless:  claude -p --output-format stream-json --verbose
  codex         OpenAI Codex CLI, headless: codex exec --json
  noop          does nothing (the do-nothing baseline every benchmark should report)
  oracle        applies the task's reference solution (used by `repoevals check`)
  cmd:<template>  any CLI; {prompt_file} {workspace} {model} are substituted, stdout becomes the answer

Isolation: `--docker IMAGE` runs the agent in `docker run --network none` with only the workspace mounted.
Without it, the agent runs as a local subprocess in the workspace — fine for trusted local experiments,
not for CI on untrusted code.
"""
from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from .trace import CONVERTERS, write_events
from .util import fmt, run


@dataclass
class AgentResult:
    rc: int
    seconds: float
    timed_out: bool
    answer: str


def _docker_wrap(argv: list[str], ws: Path, image: str, network: str = "none", env: dict | None = None,
                 name: str = "") -> list[str]:
    cmd = ["docker", "run", "--rm", "-i", f"--network={network}", "-v", f"{ws.resolve()}:/workspace",
           "-w", "/workspace"] + (["--name", name] if name else [])
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    return cmd + [image] + argv


def run_agent(agent: str, run_dir: Path, model: str = "", timeout: float = 3600, docker: str = "",
              network: str = "none", extra_args: str = "", env: dict | None = None) -> AgentResult:
    ws = run_dir / "workspace"
    prompt = (run_dir / "instruction.md").read_text()
    if agent == "noop":
        (run_dir / "answer.txt").write_text("")
        write_events(run_dir, [])
        return AgentResult(0, 0.0, False, "")
    if agent == "oracle":
        raise ValueError("the oracle agent is applied by `repoevals check`, not run_agent")
    converter = None
    if agent == "claude-code":
        argv = ["claude", "-p", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "bypassPermissions"] + (["--model", model] if model else [])
        converter = "claude"
    elif agent == "codex":
        argv = ["codex", "exec", "--json", "--skip-git-repo-check", "-s", "workspace-write"] + \
               (["-m", model] if model else []) + ["-"]
        converter = "codex"
    elif agent.startswith("cmd:"):
        pf = run_dir / "instruction.md"
        argv = shlex.split(fmt(agent[4:], prompt_file=str(pf.resolve()), workspace=str(ws.resolve()), model=model))
    else:
        raise ValueError(f"unknown agent {agent!r}: claude-code | codex | noop | cmd:<template>")
    argv += shlex.split(extra_args)
    container = ""
    if docker:
        import uuid
        container = f"repoevals-{uuid.uuid4().hex[:12]}"
        argv = _docker_wrap(argv, ws, docker, network, env, container)
    t0 = time.time()
    p = run(argv, cwd=ws, timeout=timeout, env=env, stdin=prompt)  # kills the whole process group on timeout
    if container and p.timed_out:  # the container outlives a killed `docker run` client
        run(["docker", "kill", container])
    (run_dir / "agent_stdout.log").write_text(p.out)
    if converter:
        events, answer = CONVERTERS[converter](p.out.splitlines())
    else:
        events, answer = [], p.out
    write_events(run_dir, events)
    (run_dir / "answer.txt").write_text(answer or "")
    return AgentResult(p.rc, round(time.time() - t0, 2), p.timed_out, answer or "")


def record(run_dir: Path, agent: str, model: str, res: AgentResult) -> None:
    from .workspace import update_meta
    update_meta(run_dir, agent=agent, model=model or None, agent_rc=res.rc, agent_seconds=res.seconds,
                agent_timed_out=res.timed_out)


def load_answer(run_dir: Path) -> str:
    p = run_dir / "answer.txt"
    return p.read_text() if p.exists() else ""


def dump(obj) -> str:
    return json.dumps(obj, indent=1, default=str)
