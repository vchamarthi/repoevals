# Configuration

repoevals reads `repoevals.toml`, searching upward from the current directory. `.agent-evals/repoevals.toml` is
also accepted. Use `--config PATH` to point at a specific file.

## Full example

```toml
[repo]
id = "dpnp"
url = "https://github.com/IntelPython/dpnp"
tasks = ".agent-evals/tasks"
graders = ".agent-evals/graders"
hide = []

[guidance]
globs = ["**/AGENTS.md", "**/CLAUDE.md", ".github/copilot-instructions.md", ".github/instructions/*.md"]

[policy]
test_globs = ["dpnp/tests/**"]
library_globs = ["dpnp/**"]
protected_globs = ["dpnp/tests/skipped_tests*.tbl"]
formatter = "pre-commit run --files {files}"

[test]
adapter = "pytest"
build = "pip install -e . --no-build-isolation -q"
command = "python -m pytest -q -p no:randomly {targets}"
timeout_sec = 1800
env = { ONEAPI_DEVICE_SELECTOR = "opencl:cpu", OMP_NUM_THREADS = "2" }

[environment]
image = "ghcr.io/your-org/agent-image:2026.10"
dockerfile = ".agent-evals/Dockerfile"
network = "none"

[integrity]
gate = false
```

## `[repo]`

| key | default | description |
|---|---|---|
| `id` | directory name | name used in reports |
| `url` | `""` | project URL, informational |
| `tasks` | `".agent-evals/tasks"` | directory containing one sub-directory per task |
| `graders` | `""` | directory of repository-specific grader plugins (see [Extending](extending.md)) |
| `hide` | `[]` | additional globs removed from the agent's workspace |

The tasks directory, the graders directory, `.agent-evals/` and `repoevals.toml` are always removed from the
agent's workspace.

## `[guidance]`

| key | default | description |
|---|---|---|
| `globs` | AGENTS.md, CLAUDE.md, GEMINI.md, Copilot and Cursor instruction files | agent guidance files: removed or replaced by the `guidance:none` and `guidance:file=` arms, and checked by `guidance-lint` |

## `[policy]`

| key | default | description |
|---|---|---|
| `test_globs` | common test locations | files that count as tests. Edits to existing tests are restored before grading fix tasks; test-writing tasks keep only these files |
| `library_globs` | `[]` | library source files |
| `protected_globs` | `[]` | files restored to their original content before every grade, e.g. skip or deselect lists |
| `formatter` | `""` | command checking formatting; `{files}` is replaced by the changed files. Reported as the `format_ok` metric |

Globs use `**` for any number of directories and `*` within one path segment.

## `[test]`

| key | default | description |
|---|---|---|
| `adapter` | `"pytest"` | `pytest`, `bazel`, `ctest` or `command` |
| `build` | `""` | shell command run before tests to make the tree testable |
| `command` | adapter default | test command; `{targets}` is replaced by the task's test targets |
| `timeout_sec` | `1800` | time limit for a build or test run |
| `env` | `{}` | environment variables for builds and tests |
| `rc_map` | `{}` | `command` adapter only: map exit codes to statuses, e.g. `{ "0" = "passed", "1" = "failed", "2" = "build_error" }` |

Tasks can override `adapter`, `build`, `test_command` and `test_env` in their `[grader]` table.

### Test adapters

| adapter | targets | default command | outcome |
|---|---|---|---|
| `pytest` | node ids or paths | `python -m pytest -q -p no:cacheprovider {targets}` | per-test results from a JUnit report; exit 2 is a build error, 5 is no tests; a run that passes nothing (everything skipped) or writes no report is not a pass |
| `bazel` | labels | `bazel test --nocache_test_results --test_output=errors {targets}` | exit 1 build error, 3 tests failed, 4 no tests |
| `ctest` | name regexes | `ctest --test-dir build --output-on-failure {targets}` | use `build` to configure and compile |
| `command` | anything | `{targets}` | exit code mapped through `rc_map` (0 = passed, otherwise failed) |

Targets are always shell-quoted before they are substituted.

## `[environment]`

| key | default | description |
|---|---|---|
| `image` | `""` | container image used by `repoevals run --docker` without an explicit image |
| `network` | `"none"` | Docker network mode for agent containers |
| `dockerfile` | `""` | Dockerfile copied into tasks exported with `repoevals export harbor` |

## `[integrity]`

| key | default | description |
|---|---|---|
| `gate` | `false` | when `true`, any integrity violation fails the task. Otherwise violations are reported only |

## Environment variables

| variable | description |
|---|---|
| `REPOEVALS_DEVICES` | comma-separated devices available on this host (default `cpu`). Tasks requiring other devices are skipped |
