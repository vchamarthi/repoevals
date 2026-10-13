# GitHub Action

```yaml
- uses: vchamarthi/repoevals@v0.1.0
  with:
    command: check
```

The action installs repoevals and runs one command. The repository must be checked out with full history
(`fetch-depth: 0`), because tasks start from earlier revisions. Install your project's test dependencies in a step
before the action.

## Inputs

| input | default | description |
|---|---|---|
| `command` | `check` | `validate`, `check`, `run`, `report` or `guidance-lint` |
| `tasks` | all | space-separated task selection |
| `changed-only` | `true` | `check` on pull requests: only tasks whose files changed; all tasks if none changed |
| `config` | search | path to `repoevals.toml` |
| `repeats` | `1` | `check`: reference-solution repeats. `run`: attempts per task and arm |
| `agent` | `noop` | `run`: agent to use |
| `model` | | `run`: model id |
| `arms` | `guidance:raw` | `run`: space-separated arms |
| `runs-dir` | `repoevals-runs` | `run` and `report`: output directory |
| `python-version` | `3.12` | Python used to run repoevals |
| `repoevals-version` | the action's version | pip requirement to install instead |

## Outputs

| output | description |
|---|---|
| `report` | path of `summary.md` (`run` and `report`) |

Results of `check` and `report` are also written to the job summary.

## Recipes

### Validate and self-check on pull requests

No model and no secrets are used, so this is safe for pull requests from forks.

```yaml
name: agent-evals
on:
  pull_request:
    paths: [".agent-evals/**", "repoevals.toml", "**/AGENTS.md", "**/CLAUDE.md"]
  schedule:
    - cron: "17 3 * * 1"            # weekly: every task
permissions:
  contents: read
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: vchamarthi/repoevals@v0.1.0
        with:
          command: validate
      - uses: vchamarthi/repoevals@v0.1.0
        with:
          command: guidance-lint
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[test]"
      - uses: vchamarthi/repoevals@v0.1.0
        with:
          command: check
          repeats: "2"
```

### Run an agent on demand

```yaml
name: agent-evals-run
on:
  workflow_dispatch:
    inputs:
      arms:
        default: "guidance:raw guidance:none"
permissions:
  contents: read
jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 360
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - run: pip install -e ".[test]" && npm install -g @anthropic-ai/claude-code
      - uses: vchamarthi/repoevals@v0.1.0
        id: evals
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        with:
          command: run
          agent: claude-code
          arms: ${{ inputs.arms }}
          repeats: "5"
      - uses: actions/upload-artifact@v4
        with:
          name: agent-evals
          path: repoevals-runs/
```

Never run agents on `pull_request_target` or with secrets exposed to untrusted pull requests.
