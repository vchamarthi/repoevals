"""Agents under test. An agent turns (workspace, instruction) into an edited workspace + final answer + trace.

Built-in agents:
  claude-code   Claude Code CLI, headless:  claude -p --output-format stream-json --verbose
  codex         OpenAI Codex CLI, headless: codex exec --json
  noop          does nothing (the do-nothing baseline every benchmark should report)
  cmd:<template>  any CLI; {prompt_file} {workspace} {model} are substituted, stdout becomes the answer

Every attempt gets an `agent_status`:
  ok           the agent ran to completion (its result is graded)
  timeout      the agent hit its time limit (graded: whatever it left is the result)
  infra_error  the agent could not run: CLI missing, authentication, rate limit, overload, API outage.
               Not the agent's fault: the attempt is retried (`run --retries`) and otherwise recorded as `error`.

Containers (`--docker IMAGE`): the workspace is mounted at the same absolute path, the agent runs as the host user,
API credentials listed in `[environment].env_passthrough` are passed in, and the network mode comes from
`[environment].network` (default "bridge": API agents need to reach their model API).
"""
from __future__ import annotations

import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

from .trace import CONVERTERS, write_events
from .util import fmt, run

DEFAULT_PASSTHROUGH = ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY",
                       "OPENAI_BASE_URL", "GEMINI_API_KEY"]
# Claude Code on a cloud provider authenticates with these; they are passed only when the provider switch is set
CLOUD_PASSTHROUGH = {
    "CLAUDE_CODE_USE_BEDROCK": ["CLAUDE_CODE_USE_BEDROCK", "AWS_BEARER_TOKEN_BEDROCK", "AWS_REGION", "AWS_DEFAULT_REGION",
                                "AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"],
    "CLAUDE_CODE_USE_VERTEX": ["CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION",
                               "GOOGLE_APPLICATION_CREDENTIALS"],
}


def default_passthrough(environ=None) -> list[str]:
    """Variables the agent may keep when `[environment].env_passthrough` is not set."""
    environ = os.environ if environ is None else environ
    extra = [v for switch, names in CLOUD_PASSTHROUGH.items() if environ.get(switch) not in (None, "", "0")
             for v in names]
    return [*DEFAULT_PASSTHROUGH, *extra]


# the agent's final message itself says it could not work (usage caps end the session with exit code 0)
LIMIT_ANSWER = re.compile(
    r"^\W*((you('ve| have)? )?(hit|reached) your (session|usage|weekly|daily) limit|usage limit (reached|exceeded)|"
    r"rate limit (reached|exceeded)|credit balance is too low|quota exceeded|please run /login|not logged in|"
    r"invalid api key)"
    r"(\W+(resets?\b[^\n]*|please run /login\W*|try again[^\n]*|upgrade[^\n]*))?\W*$", re.I)
INFRA_PATTERNS = re.compile(
    r"(rate[ _-]?limit|session limit|usage limit|"
    r"429|overloaded|529|authenticat|unauthori[sz]ed|401\b|invalid api key|api key|credit balance|"
    r"quota|insufficient_quota|service unavailable|503\b|502\b|connection (refused|reset|error)|ENOTFOUND|"
    r"ECONNRESET|\"subtype\":\"api_retry\"|API Error: \d{3}|not logged in|please run .*login|command not found|"
    r"No such file or directory: '(claude|codex)')",
    re.I)
VERSION_ARGS = {"claude-code": ["claude", "--version"], "codex": ["codex", "--version"]}


@dataclass
class AgentResult:
    rc: int
    seconds: float
    timed_out: bool
    answer: str
    status: str = "ok"
    reason: str = ""
    model_resolved: str = ""
    extra: dict = field(default_factory=dict)


def agent_version(agent: str) -> str:
    argv = VERSION_ARGS.get(agent)
    if not argv:
        return ""
    p = run(argv, timeout=30)
    return p.out.strip().splitlines()[0][:100] if p.rc == 0 and p.out.strip() else ""


def classify(rc: int, timed_out: bool, out: str, answer: str, events: list[dict],
             traced: bool = True) -> tuple[str, str]:
    """(agent_status, reason) from the CLI's exit code, output and trace."""
    if timed_out:
        return "timeout", "time limit reached"
    if rc == 127:
        return "infra_error", "agent CLI not found"
    m = LIMIT_ANSWER.match(answer.strip())  # the whole message is the notice, not a review mentioning it
    if m and len(answer.strip()) < 600:  # a short final message that is only the limit/auth notice
        return "infra_error", f"agent stopped: {m.group(0)}"
    if not traced:  # cmd: agents may legitimately work silently; only clear CLI failures count
        m = INFRA_PATTERNS.search(out[-4000:]) if rc != 0 else None
        return ("infra_error", f"agent CLI reported: {m.group(0)}") if m else ("ok", "")
    acted = any(e.get("kind") in ("command", "edit", "read", "search") for e in events)
    if rc != 0 or (not answer.strip() and not acted):
        tail = out[-4000:]
        m = INFRA_PATTERNS.search(tail)
        if m and not acted:
            return "infra_error", f"agent CLI reported: {m.group(0)}"
        if not acted and not answer.strip():
            return "infra_error", f"agent produced no output (rc={rc})"
    return "ok", ""


def _docker_wrap(argv: list[str], ws: Path, image: str, network: str, env_names: list[str], name: str,
                 extra_mounts: list[Path]) -> list[str]:
    ws = ws.resolve()
    cmd = ["docker", "run", "--rm", "-i", f"--network={network}", "--name", name, "-v", f"{ws}:{ws}", "-w", str(ws)]
    for p in extra_mounts:
        cmd += ["-v", f"{p.resolve()}:{p.resolve()}:ro"]
    if hasattr(os, "getuid"):
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"]
    for k in env_names:
        if k in os.environ:
            cmd += ["-e", k]  # value taken from the host environment, never written to the command line
    return cmd + [image] + argv


def run_agent(agent: str, run_dir: Path, model: str = "", timeout: float = 3600, docker: str = "",
              network: str = "bridge", extra_args: str = "", env: dict | None = None,
              env_passthrough: list[str] | None = None) -> AgentResult:
    ws = run_dir / "workspace"
    prompt_file = run_dir / "instruction.md"
    prompt = prompt_file.read_text()
    if agent == "noop":
        (run_dir / "answer.txt").write_text("")
        write_events(run_dir, [])
        return AgentResult(0, 0.0, False, "")
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
        argv = shlex.split(fmt(agent[4:], prompt_file=str(prompt_file.resolve()), workspace=str(ws.resolve()),
                               model=model))
    else:
        raise ValueError(f"unknown agent {agent!r}: claude-code | codex | noop | cmd:<template>")
    argv += shlex.split(extra_args)
    passthrough = default_passthrough() if env_passthrough is None else list(env_passthrough)  # [] means none
    container = ""
    if docker:
        import uuid
        container = f"repoevals-{uuid.uuid4().hex[:12]}"
        argv = _docker_wrap(argv, ws, docker, network, passthrough, container,
                            [prompt_file])
    t0 = time.time()
    log = run_dir / "agent_stdout.log"
    # credentials are removed from the agent's environment except those it needs to reach its model API
    p = run(argv, cwd=ws, timeout=timeout, env=env, stdin=prompt, scrub=True,
            keep_env=[*passthrough, "HOME", "PATH"], output_file=log)
    if container and p.timed_out:  # the container outlives a killed `docker run` client
        run(["docker", "kill", container])
    if converter:
        with open(log, errors="replace") as fh:
            events, answer = CONVERTERS[converter](fh.read().splitlines())
    else:
        events, answer = [], p.out
    write_events(run_dir, events)
    (run_dir / "answer.txt").write_text(answer or "")
    status, reason = classify(p.rc, p.timed_out, p.out, answer or "", events, traced=converter is not None)
    model_resolved = next((e.get("model", "") for e in events if e.get("kind") == "session" and e.get("model")), "")
    return AgentResult(p.rc, round(time.time() - t0, 2), p.timed_out, answer or "", status, reason, model_resolved)


def record(run_dir: Path, agent: str, model: str, res: AgentResult, attempts: int = 1, version: str = "") -> None:
    from .workspace import update_meta
    update_meta(run_dir, agent=agent, model=model or res.model_resolved or None, model_resolved=res.model_resolved
                or None, agent_version=version or None, agent_rc=res.rc, agent_seconds=res.seconds,
                agent_timed_out=res.timed_out, agent_status=res.status, agent_reason=res.reason or None,
                agent_attempts=attempts)
