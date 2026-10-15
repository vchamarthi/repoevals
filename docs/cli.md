# Command line

```
repoevals [--config PATH] <command> [options]
```

`--config` may be given before or after the command. Without it, `repoevals.toml` is searched upward from the
current directory.

Task selection (`[tasks ...]`) accepts task ids, prefixes ending in `*`, `family:<name>` and `tag:<name>`.

## Commands

### `init [--preset NAME]`

Create `repoevals.toml` and an empty `.agent-evals/tasks/`. `--preset` starts from a starter configuration
(`dpnp`, `dpctl`, `mkl_fft`, `mkl_random`, `mkl_umath`, `scikit-learn-intelex`, `xgboost`, `onedal`).

### `doctor [tests ...] [--agent A] [--model M]`

Check the setup: configuration, git history, tasks, test adapter, agent CLIs, `gh`, Docker, and whether guidance files
above the runs directory would leak into every arm. With test targets, also runs them on the current tree. With `--agent`, sends one trivial prompt through the same code path and credential handling as `run`, which catches a logged-out CLI, a CLI too old for the model and missing credentials.

### `list [tasks ...]`

List tasks with their family, grader, base revision and creation date.

### `validate [tasks ...] [--source DIR]`

Static checks of task definitions: required fields and files, known grader, gate syntax, valid JSON, and
instructions that may reveal the solution. With `--source`, also checks that every base revision exists and that
patches apply to it. Takes seconds; no tests are run.

### `check [tasks ...] [--repeats N] [--source DIR] [--keep DIR] [--json FILE]`

Proves each task is valid by running its grader without an agent:

| probe | must |
|---|---|
| untouched workspace | fail |
| reference solution, `--repeats` times | pass every time |
| reference tests against mutants (`mutation`) | reach `oracle_min_kill` |
| one finding on every changed line (review tasks) | fail |

`--keep` keeps the run directories for inspection; `--json` writes the per-task results and the installed package versions to a file.

### `run --agent AGENT [tasks ...] [options]`

Prepare a workspace, run the agent, and grade, for every selected task × arm × repeat.

| option | default | description |
|---|---|---|
| `--agent` | required | `claude-code`, `codex`, `noop` (does nothing; a baseline) or `cmd:<template>` |
| `--model` | agent default | model id passed to the agent |
| `--arm` | `guidance:raw` | repeatable: `guidance:raw`, `guidance:none`, `guidance:file=PATH` |
| `--repeats` | `3` | attempts per task and arm |
| `--docker [IMAGE]` | off | run the agent in a container; without a value, uses `[environment].image` |
| `--agent-args` | | extra arguments for the agent CLI |
| `--source` | config repository | repository to create workspaces from |
| `--out` | `~/.cache/repoevals/runs/<repo>/<timestamp>` | output directory; `$REPOEVALS_RUNS_DIR` changes the default root. Must be outside the repository |
| `--allow-out-in-repo` | off | allow `--out` inside the repository (agents could then read hidden material) |
| `--include-drafts` | off | also run tasks with `status = "draft"` |
| `--jobs` | `1` | attempts run in parallel. Builds and installs must not share mutable state (see [Configuration](configuration.md#environment)). `run` refuses `--jobs` above 1 when `setup` or `build` installs the project in editable mode into the shared environment |
| `--resume` | off | keep attempts in `--out` that already have a grade (errors are redone) |
| `--retries` | `2` | retries, with backoff, when the agent CLI fails for infrastructure reasons |
| `--max-cost` | none | stop starting new attempts once reported cost reaches this many US dollars |
| `--label` | none | recorded with every result and shown in reports, e.g. a release name |
| `--shuffle`, `--seed` | off, `0` | randomize the order of attempts |

In `cmd:` templates, `{prompt_file}`, `{workspace}` and `{model}` are substituted. The agent's standard output becomes
its final answer. `cmd:` agents produce no trace, so trace-based integrity checks do not apply to them; reports mark
such configurations.

Each attempt records an agent status: `ok`, `timeout` (graded as left), or `infra_error` (missing CLI,
authentication, rate limit, overload, no output), which is retried and otherwise recorded as status `error`.
`<out>/run.json` records the arguments, agent version, source revision, counts and cost of the whole run.

With `--docker`, the workspace is mounted at the same path, the agent runs as your user, credentials named in
`[environment].env_passthrough` are passed in, and the network mode is `[environment].network` (default `bridge`,
because API-based agents must reach their model API). The image must contain the agent CLI, git and your project's
toolchain.

Arms:

| arm | the agent's workspace contains |
|---|---|
| `guidance:raw` | your guidance files as committed |
| `guidance:none` | no guidance files |
| `guidance:file=PATH` | only `PATH`, installed as both `AGENTS.md` and `CLAUDE.md` |
| `guidance:overlay=DIR` | your guidance plus the files in `DIR` (added or replaced), e.g. a draft from `guidance suggest` |
| `<name>` | as defined in `[arms.<name>]` (see [Configuration](configuration.md#arms)) |

### `report RUNS [--out DIR] [--fail-under RATE] [--compare DIMENSION]`

Aggregate every `grade.json` under `RUNS` into `summary.md`, `results.json` and `results.jsonl` (default
`RUNS/report`). With `--fail-under`, exit 1 when any configuration's pass rate is below `RATE`. With `--compare arm`
(or `label`, `agent`, `model`), only configurations differing in that one dimension are compared. See
[Reports and statistics](reports.md).

### `show RUN_DIR [--diff] [--answer]`

Summarize one attempt: configuration, agent status and time, result and metrics, failing tests with log tails,
process data, integrity findings and the files the agent changed. `--diff` prints the agent's full diff
(`agent.diff`), `--answer` its final message.

### `prepare TASK RUN_DIR [--arm ARM] [--source DIR]`

Create the workspace and run directory for one task without running an agent.

### `grade RUN_DIR [--integrity-only]`

Grade a run directory and write `grade.json`. With `--integrity-only`, only the integrity checks of already graded runs are recomputed (`RUN_DIR` may be a directory of runs); no builds or tests run. Use this with your own agent harness; see
[Run directories](extending.md#run-directories).

### `export harbor [tasks ...] --out DIR`

Experimental. Write tasks in the Harbor task format. See [Extending](extending.md#harbor-export).

### `author <subcommand>`

Task authoring. Generated tasks are drafts (`status = "draft"`): `run` skips them unless `--include-drafts` is
given, and `validate` warns about them. See [Creating tasks](authoring.md).

| subcommand | description |
|---|---|
| `mine [--since DATE] [--limit N] [--top N] [--json FILE] [--release-base TAG]` | rank commits that changed library code and tests together; with `--release-base`, only non-compiled fixes after TAG that apply at TAG |
| `fix COMMIT [--id ID] [--family fix\|feature] [--base REV] [--extract-tests] [--force]` | scaffold a hidden-tests task from a commit, optionally at another base and with only the changed test functions |
| `review-replay PR [--repo OWNER/NAME] [--id ID] [--base REV] [--force]` | scaffold a review-replay task from a GitHub pull request (needs `gh`) |
| `mutants TASK FILE [--function NAME] [--max N] [--base REV]` | generate mutant patches for a Python file; creates the test-writing task if needed |
| `prune-mutants TASK` | remove mutants the task's reference tests do not detect |
| `approve TASK...` | set `status = "ready"`; refuses tasks whose instruction is still the template |
| `pin [TASK...]` | replace symbolic bases (`HEAD`, branch names) with full commit SHAs |
| `playbook` | print the agent playbook |
| `prompt --repo URL [--count N] [--families TEXT] [--workdir DIR]` | print a ready-to-use prompt for an agent |
| `install [--no-claude] [--codex] [--codex-home DIR]` | install the playbook as a Claude Code skill and/or a Codex custom prompt |

### `guidance <subcommand>`

Improve agent guidance files. See [Improving agent guidance](guidance.md).

| subcommand | description |
|---|---|
| `suggest RUNS [--out DIR] [--draft-with AGENT] [--model M] [--min-runs N]` | mine graded runs for problems guidance could prevent; write `evidence.md`, `evidence.json` and a `draft/` directory |
| `verify DRAFT [tasks ...] --agent A [--repeats N] [--baseline ARM] [--jobs N] [--exclude-tasks-in RUNS] [--resume]` | run tasks with the current guidance and with the draft overlaid; print the paired difference and write `verify.md` |
| `lint [--strict]` | same as `guidance-lint` |

### `guidance-lint [--strict]`

Report file paths mentioned in agent guidance files that do not exist in the repository.

## Exit codes

| command | 0 | 1 | 2 |
|---|---|---|---|
| `validate` | no errors | errors, or no tasks found | |
| `check` | every task valid | at least one task has a problem | |
| `grade` | graded (pass or fail) | | not graded (error) |
| `run` | the matrix ran (individual attempts may have failed or errored) | no tasks to run | usage error, or `--out` inside the repository |
| `report` | report written | `--fail-under` not met | |
| `doctor` | no blocking problem | a check failed | |
| `guidance-lint` | always, unless `--strict` | `--strict` and missing paths | |
| `export`, `list`, `prepare`, `show`, `author` | success | | usage error |
