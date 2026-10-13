# Command line

```
repoevals [--config PATH] <command> [options]
```

`--config` may be given before or after the command. Without it, `repoevals.toml` is searched upward from the
current directory.

Task selection (`[tasks ...]`) accepts task ids, prefixes ending in `*`, `family:<name>` and `tag:<name>`.

## Commands

### `init`

Create `repoevals.toml` and an example task in `.agent-evals/tasks/example_fix/`.

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

`--keep` keeps the run directories for inspection; `--json` writes the results to a file.

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
| `--out` | `runs/<timestamp>` | output directory |

In `cmd:` templates, `{prompt_file}`, `{workspace}` and `{model}` are substituted. The agent's standard output becomes
its final answer.

Arms:

| arm | the agent's workspace contains |
|---|---|
| `guidance:raw` | your guidance files as committed |
| `guidance:none` | no guidance files |
| `guidance:file=PATH` | only `PATH`, installed as `AGENTS.md` |

### `report RUNS [--out DIR]`

Aggregate every `grade.json` under `RUNS` into `summary.md`, `results.json` and `results.jsonl` (default
`RUNS/report`). See [Reports and statistics](reports.md).

### `prepare TASK RUN_DIR [--arm ARM] [--source DIR]`

Create the workspace and run directory for one task without running an agent.

### `grade RUN_DIR`

Grade a run directory and write `grade.json`. Use this with your own agent harness; see
[Run directories](extending.md#run-directories).

### `export harbor [tasks ...] --out DIR`

Write tasks in the Harbor task format. See [Extending](extending.md#harbor-export).

### `guidance-lint [--strict]`

Report file paths mentioned in agent guidance files that do not exist in the repository.

## Exit codes

| command | 0 | 1 | 2 |
|---|---|---|---|
| `validate` | no errors | errors, or no tasks found | |
| `check` | every task valid | at least one task has a problem | |
| `grade` | graded (pass or fail) | | not graded (error) |
| `guidance-lint` | always, unless `--strict` | `--strict` and missing paths | |
| `run`, `report`, `export`, `list`, `prepare` | success | | usage error |
