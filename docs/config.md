# heldout.toml reference

Looked up from the current directory upwards: `heldout.toml`, then `.agent-evals/heldout.toml`.

```toml
[repo]
id = "dpnp"                                   # used in reports
url = "https://github.com/IntelPython/dpnp"
tasks = ".agent-evals/tasks"                  # removed from the agent's workspace
graders = ".agent-evals/graders"              # optional repo-local grader plugins (*.py with GRADERS/ORACLES)

[guidance]                                    # files the guidance arms add/remove; also scanned by guidance-lint
globs = ["**/AGENTS.md", "**/CLAUDE.md", ".github/copilot-instructions.md", ".github/instructions/*.md"]

[policy]
test_globs = ["dpnp/tests/**"]                # test files (integrity checks, mutation, reset of edited tests)
library_globs = ["dpnp/**"]                   # library files (reset before mutation grading)
protected_globs = ["dpnp/tests/skipped_tests*.tbl"]   # reset to pristine before every grade
formatter = "pre-commit run --files {files}"  # optional: format_ok metric (never a gate unless you gate it)

[test]
adapter = "pytest"                            # pytest | bazel | ctest | command
build = "pip install -e . --no-build-isolation -q"
command = "python -m pytest -q -p no:randomly {targets}"   # adapter default if empty
timeout_sec = 1800
env = { ONEAPI_DEVICE_SELECTOR = "opencl:cpu", OMP_NUM_THREADS = "2" }
rc_map = { "0" = "passed", "1" = "failed", "2" = "build_error" }   # command adapter only

[environment]
image = "ghcr.io/org/repo-agent-evals:2026.10"   # used by `run --docker` and harbor export
dockerfile = ".agent-evals/Dockerfile"
network = "none"

[integrity]
gate = false                                  # true: integrity violations fail tasks repo-wide
```

## Adapters

| adapter | targets | status mapping |
|---|---|---|
| `pytest` | node ids / files | rc 0 passed, 1 failed, 2 build_error (collection/import), 5 no_tests; per-test results from JUnit XML |
| `bazel` | labels | rc 0 passed, 1 build_error, 3 failed, 4 no_tests |
| `ctest` | `-R` regexes | `build` should configure + compile; summary line parsed |
| `command` | substituted into `{targets}` | `rc_map`, default rc 0 passed / otherwise failed |

## Environment variables

| variable | meaning |
|---|---|
| `HELDOUT_DEVICES` | comma-separated devices present on this host (default `cpu`), matched against `requires.devices` |
