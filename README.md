# repoevals

Repository-native evaluations for AI coding agents.

[![CI](https://github.com/vchamarthi/repoevals/actions/workflows/ci.yml/badge.svg)](https://github.com/vchamarthi/repoevals/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)
![License](https://img.shields.io/badge/license-Apache--2.0-green)

repoevals measures how well AI coding agents work on **your** codebase. You keep a small config file and a folder
of tasks in your repository; repoevals prepares an isolated workspace for each task, runs the agent, and grades the
result with tests and checks the agent never sees. Grading is deterministic and uses no LLM, so the same run always
gets the same score.

- **Your tasks, your code** — bug fixes, features, builds, code reviews, test writing, numerical accuracy and
  performance work, defined next to the code they exercise.
- **Hidden answers** — hidden tests, reference solutions, mutants and review manifests stay outside the agent's
  workspace, which contains no git history.
- **Deterministic grading** — test runs, mutation analysis, matching against known review findings, and numerical
  comparison against a trusted reference.
- **Self-checking tasks** — `repoevals check` proves that every task fails when nothing is done and passes with the
  reference solution. It needs no model and no API key, so it is safe to run on every pull request.
- **Cheating detection** — test tampering, git-history mining, network access and edits to protected files are
  flagged on every graded run.
- **Comparisons you can trust** — repeated trials, pass@k, task-clustered confidence intervals and paired
  comparisons between configurations, with cost and token usage.
- **Any agent, any stack** — Claude Code, Codex, or any command-line agent; pytest, Bazel, CTest or any test command.

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Concepts](#concepts)
- [Task types](#task-types)
- [Running agents](#running-agents)
- [Reading results](#reading-results)
- [Continuous integration](#continuous-integration)
- [Documentation](#documentation)
- [Getting help](#getting-help)
- [Contributing](#contributing)
- [License](#license)

## Installation

Requirements: Python 3.10 or newer, and git. repoevals has no runtime dependencies on Python 3.11+.

```bash
pip install "repoevals @ git+https://github.com/vchamarthi/repoevals@v0.1.0"
```

Numerical tasks additionally need NumPy in the environment where your project's tests run.

To run agents you also need the agent's CLI installed and authenticated, for example `claude` (Claude Code) or
`codex` (OpenAI Codex CLI).

## Quick start

### 1. Initialize your repository

```bash
cd your-project
repoevals init
```

This creates `repoevals.toml` and an example task in `.agent-evals/tasks/example_fix/`. Edit the config so it
describes how your tests run:

```toml
[repo]
id = "your-project"
tasks = ".agent-evals/tasks"

[policy]
test_globs = ["tests/**"]
library_globs = ["your_package/**"]

[test]
adapter = "pytest"
build = "pip install -e . -q"
```

### 2. Write a task

A task is a directory with a `task.toml`, the instruction the agent receives, and the material used to grade it.
This bug-fix task re-introduces a bug with `setup.patch` and grades the fix with a test the agent never sees:

```
.agent-evals/tasks/fix_var_ddof/
├── task.toml
├── instruction.md          # the bug report, as a user would write it
├── setup.patch             # applied before the agent starts; its reverse is the reference fix
└── hidden/
    └── tests/test_var_hidden.py
```

```toml
# task.toml
version = "1.0"

[metadata]
family = "fix"
base = "HEAD"               # any git revision of your repository
created = "2026-10-13"

[grader]
type = "hidden_tests"
targets = ["tests/test_var_hidden.py"]
regression_targets = ["tests/test_basic.py"]
```

### 3. Validate and self-check

```bash
repoevals validate           # static checks: schema, files, instruction hygiene
repoevals check              # the untouched workspace must fail, the reference solution must pass
```

```
OK       fix_var_ddof: null=fail oracle=['pass']
```

### 4. Run an agent and read the report

```bash
repoevals run --agent claude-code --repeats 5
repoevals report runs/<timestamp>
```

A complete, runnable example repository with one task of each type is in
[`examples/minisci`](examples/minisci).

## Concepts

| term | meaning |
|---|---|
| **task** | One directory under your tasks folder: what the agent is asked to do and how the result is graded. |
| **base** | The git revision the task starts from. Tasks can start from any commit in your history. |
| **workspace** | A fresh copy of the repository at the base, with task files and git history removed. The agent works only here. |
| **grader** | The deterministic check that turns the agent's final workspace and answer into a result. |
| **reference solution** | A patch or set of files that solves the task. `repoevals check` uses it to prove the task is solvable. |
| **arm** | A variant of the setup to compare, e.g. with and without your `AGENTS.md`. |
| **run directory** | Everything about one attempt: the workspace, the instruction, the agent's trace and answer, and `grade.json`. |

Each graded run has a status: `pass`, `fail`, `skipped` (the host lacks required hardware), `scored` (a metric
without a pass/fail gate), or `error` (infrastructure problem, not counted against the agent).

## Task types

| family | grader | the agent is asked to | graded by |
|---|---|---|---|
| `fix`, `feature` | `hidden_tests` | fix a bug or add behaviour | hidden tests plus existing suites; modified tests are restored before grading |
| `build` | `command` | build, install or configure | commands succeed, artifacts exist, optionally an unchanged tree |
| `test` | `mutation` | write tests for existing code | tests pass on the real code and detect a minimum share of known faults |
| `review` | `review_seeded` | review a change with known problems | recall and precision against known findings; decoys and clean changes catch false alarms |
| `review` | `review_replay` | review a real past pull request | recall and precision against the human review comments |
| `numeric` | `numeric` | make a numerical routine accurate | comparison with a trusted reference per dtype, including NaN/Inf handling and ULP error |
| `perf` | `perf` | make code faster | correctness first, then a measured speedup with a confidence interval |

You can add your own graders. See [Extending repoevals](docs/extending.md).

## Running agents

```bash
# Claude Code, three repeats per task
repoevals run --agent claude-code --repeats 3

# Codex, a specific model, only bug-fix tasks
repoevals run --agent codex --model <model-id> family:fix

# Any command-line agent; {prompt_file}, {workspace} and {model} are substituted
repoevals run --agent "cmd:my-agent --prompt-file {prompt_file}"

# Compare your agent guidance against none, on the same tasks
repoevals run --agent claude-code --arm guidance:raw --arm guidance:none --repeats 8

# Run the agent inside a container with no network access
repoevals run --agent claude-code --docker ghcr.io/your-org/agent-image:latest
```

Without `--docker`, the agent runs as a local process with your user's permissions. Use a container for untrusted
tasks or agents.

Already have your own agent harness? Produce a run directory and call `repoevals grade <run_dir>`. See
[Run directories](docs/extending.md#run-directories).

## Reading results

`repoevals report <runs-dir>` writes three files:

| file | contents |
|---|---|
| `summary.md` | pass rate per configuration with 95% confidence intervals, paired comparisons, per-task pass@k, cost and tokens, runs with integrity flags |
| `results.json` | the same numbers, machine-readable |
| `results.jsonl` | one row per graded run, ready for pandas or a database |

Read the transcripts of flagged runs before relying on a number. See [Reports and statistics](docs/reports.md).

## Continuous integration

Add the self-check to pull requests that change tasks or agent guidance:

```yaml
# .github/workflows/agent-evals.yml
name: agent-evals
on:
  pull_request:
    paths: [".agent-evals/**", "repoevals.toml", "**/AGENTS.md"]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - run: pip install -e .          # whatever makes your tests runnable
      - uses: vchamarthi/repoevals@v0.1.0
        with:
          command: check
```

The self-check uses no model and no secrets. Agent runs need API keys and belong in a manually triggered or
scheduled workflow. See [GitHub Action](docs/github-action.md).

## Documentation

| guide | |
|---|---|
| [Getting started](docs/getting-started.md) | adopt repoevals in an existing repository and write good first tasks |
| [Configuration](docs/configuration.md) | every `repoevals.toml` option and test adapter |
| [Tasks](docs/tasks.md) | task directory layout, `task.toml` fields and grader options |
| [Command line](docs/cli.md) | all commands, options and exit codes |
| [GitHub Action](docs/github-action.md) | inputs, outputs and workflow recipes |
| [Grading and integrity](docs/grading.md) | workspace isolation, self-checks and cheating detection |
| [Reports and statistics](docs/reports.md) | metrics, confidence intervals and comparisons |
| [Extending repoevals](docs/extending.md) | custom graders, run directories, Harbor export |
| [Starter configurations](examples/starter-configs) | configs for dpnp, dpctl, mkl_fft, mkl_random, mkl_umath, scikit-learn-intelex, xgboost and oneDAL |

## Getting help

- Questions and ideas: [GitHub Discussions](https://github.com/vchamarthi/repoevals/discussions)
- Bugs and feature requests: [GitHub Issues](https://github.com/vchamarthi/repoevals/issues)
- Security issues: see [SECURITY.md](SECURITY.md). Please do not open a public issue.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) to set up a development environment and open a
pull request. Maintainers follow [MAINTAINERS.md](MAINTAINERS.md). Everyone participating is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
