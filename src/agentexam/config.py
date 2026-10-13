"""Per-repository configuration: `agentexam.toml` at the repository root (or the path given with --config).

The config is the only thing a repository must own besides its task folders. Everything that is specific to a
repository's build and test tooling lives here, so graders stay generic.

    [repo]
    id = "dpnp"
    url = "https://github.com/IntelPython/dpnp"
    tasks = ".agent-evals/tasks"          # one sub-directory per task
    graders = ".agent-evals/graders"      # optional: repo-local grader plugins (*.py exporting GRADERS)
    hide = ["dev/agent_evals/**"]         # optional: more paths removed from the agent's workspace

    [guidance]                            # agent instructions; the "guidance:none" arm deletes these
    globs = ["**/AGENTS.md", "**/CLAUDE.md", ".github/copilot-instructions.md", ".github/instructions/*.md"]

    [policy]
    test_globs = ["tests/**", "**/test_*.py"]           # what counts as a test file
    library_globs = ["dpnp/**"]                          # what counts as library source
    protected_globs = ["tests/third_party/**"]           # always reset to pristine before grading
    formatter = "pre-commit run --files {files}"         # optional; exit 0 = formatted

    [test]
    adapter = "pytest"                    # pytest | bazel | ctest | command
    build = "pip install -e . --no-build-isolation -q"   # optional, run before every graded test run
    command = ""                          # adapter-specific override, {targets} substituted
    timeout_sec = 1800
    env = { ONEAPI_DEVICE_SELECTOR = "opencl:cpu" }

    [environment]                         # where agents and graders run
    image = "ghcr.io/org/repo-agent-evals:2026.10"      # or dockerfile = "..."
    network = "none"

    [integrity]
    gate = false                          # true: an integrity violation fails the task
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .util import read_toml

DEFAULT_GUIDANCE = ["**/AGENTS.md", "**/CLAUDE.md", "**/GEMINI.md", ".github/copilot-instructions.md",
                    ".github/instructions/*.md", ".cursor/rules/**", ".cursorrules"]
DEFAULT_TEST_GLOBS = ["tests/**", "test/**", "**/tests/**", "**/test_*.py", "**/*_test.py", "**/*_test.cpp",
                      "**/test/*.cpp"]
# test infrastructure the agent could use to skip or deselect tests without touching a test file; reset before
# grading by hidden_tests and included in integrity checks
TEST_INFRA_GLOBS = ["conftest.py", "**/conftest.py", "pytest.ini", "**/pytest.ini", "tox.ini", ".pytest.ini"]
CONFIG_NAMES = ("agentexam.toml", ".agent-evals/agentexam.toml")


@dataclass
class TestConfig:
    adapter: str = "pytest"
    build: str = ""
    command: str = ""
    timeout_sec: float = 1800
    env: dict[str, str] = field(default_factory=dict)
    rc_map: dict[str, str] = field(default_factory=dict)  # command adapter: {"0": "passed", "1": "failed"}


@dataclass
class RepoConfig:
    root: Path
    id: str = "repo"
    url: str = ""
    tasks: str = ".agent-evals/tasks"
    graders: str = ""
    hide: list[str] = field(default_factory=list)
    guidance_globs: list[str] = field(default_factory=lambda: list(DEFAULT_GUIDANCE))
    test_globs: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_GLOBS))
    library_globs: list[str] = field(default_factory=list)
    protected_globs: list[str] = field(default_factory=list)
    formatter: str = ""
    test: TestConfig = field(default_factory=TestConfig)
    environment: dict = field(default_factory=dict)
    integrity_gate: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def tasks_dir(self) -> Path:
        return (self.root / self.tasks).resolve()

    @property
    def graders_dir(self) -> Path | None:
        return (self.root / self.graders).resolve() if self.graders else None

    @property
    def eval_globs(self) -> list[str]:
        """Paths removed from the agent's workspace: task definitions, repo graders, this config, `[repo].hide`."""
        globs = [f"{self.tasks.rstrip('/')}/**", "agentexam.toml", ".agent-evals/**"]
        if self.graders:
            globs.append(f"{self.graders.rstrip('/')}/**")
        return globs + list(self.hide)


def find_config(start: Path) -> Path | None:
    start = start.resolve()
    for d in [start, *start.parents]:
        for name in CONFIG_NAMES:
            if (d / name).is_file():
                return d / name
    return None


def load_config(path: Path | None = None, root: Path | None = None) -> RepoConfig:
    """Load agentexam.toml. The repository root is the config's directory (or its parent for .agent-evals/)."""
    if path is None:
        path = find_config(root or Path.cwd())
    if path is None:
        return RepoConfig(root=(root or Path.cwd()).resolve())
    path = path.resolve()
    data = read_toml(path)
    cfg_root = path.parent.parent if path.parent.name == ".agent-evals" else path.parent
    repo, guid, pol = data.get("repo", {}), data.get("guidance", {}), data.get("policy", {})
    t = data.get("test", {})
    return RepoConfig(
        root=(root or cfg_root).resolve(),
        id=repo.get("id", cfg_root.name),
        url=repo.get("url", ""),
        tasks=repo.get("tasks", ".agent-evals/tasks"),
        graders=repo.get("graders", ""),
        hide=list(repo.get("hide", [])),
        guidance_globs=guid.get("globs", list(DEFAULT_GUIDANCE)),
        test_globs=pol.get("test_globs", list(DEFAULT_TEST_GLOBS)),
        library_globs=pol.get("library_globs", []),
        protected_globs=pol.get("protected_globs", []),
        formatter=pol.get("formatter", ""),
        test=TestConfig(adapter=t.get("adapter", "pytest"), build=t.get("build", ""), command=t.get("command", ""),
                        timeout_sec=float(t.get("timeout_sec", 1800)), env=dict(t.get("env", {})),
                        rc_map={str(k): v for k, v in t.get("rc_map", {}).items()}),
        environment=data.get("environment", {}),
        integrity_gate=bool(data.get("integrity", {}).get("gate", False)),
        raw=data,
    )
