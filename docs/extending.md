# Extending repoevals

## Custom graders

A grader is a function that receives a `GradeContext` and returns a dictionary of results. `"pass"` is `True`,
`False`, or `None` for a score without a pass/fail decision. Numbers and booleans become metrics; strings, lists and
dictionaries become details.

```python
# .agent-evals/graders/api_compat.py
from repoevals.graders import GradeContext, evaluate_gate
from repoevals.util import run_shell


def grade_api_compat(ctx: GradeContext) -> dict:
    p = run_shell("python tools/check_api.py --baseline api.json", cwd=ctx.workspace, timeout=600)
    out = {"api_ok": p.ok, "log_tail": p.out[-2000:]}
    out["pass"] = evaluate_gate(out, ctx.task.gate or ["api_ok"])
    return out


def oracle_api_compat(ctx: GradeContext) -> str | None:
    """Apply the reference solution to ctx.workspace; optionally return a reference final answer."""
    from repoevals.graders.tests import apply_reference
    apply_reference(ctx)
    return None


GRADERS = {"api_compat": grade_api_compat}
ORACLES = {"api_compat": oracle_api_compat}
```

Register the directory in `repoevals.toml`:

```toml
[repo]
graders = ".agent-evals/graders"
```

and use the grader in a task with `[grader] type = "api_compat"`. Providing an oracle lets `repoevals check`
validate tasks that use the grader.

Graders can also be distributed as packages through the `repoevals.graders` entry-point group:

```toml
# pyproject.toml of your package
[project.entry-points."repoevals.graders"]
api_compat = "your_package.repoevals_graders"
```

### `GradeContext`

| attribute | description |
|---|---|
| `workspace` | path of the agent's final tree |
| `task` | the task: `id`, `dir`, `family`, `base`, `grader` (the `[grader]` table), `gate`, `metadata` |
| `config` | the repository configuration |
| `start_sha` | commit the agent started from |
| `answer` | the agent's final message |
| `events` | the agent's trace |
| `g` | shortcut for `task.grader` |
| `adapter()` | the configured test adapter: `adapter().run(workspace, targets)` returns a `TestResult` |
| `tree()` | git view of the workspace: `changed(sha)`, `diff_text(sha)`, `restore(sha, paths)` |

Use `repoevals.graders.tests.TreeGuard` when a grader modifies the workspace, so the agent's tree is restored.

## Run directories

`repoevals grade` works on any run directory, so you can use your own agent harness:

```
run_dir/
├── workspace/        the agent's final tree (a git repository)
├── meta.json         {"task": "<id>", "task_dir": "<path>", "start_sha": "<commit>", "arm": "...", "agent": "...", "model": "..."}
├── answer.txt        the agent's final message
└── events.jsonl      optional trace, one JSON object per line
```

Create the workspace with `repoevals prepare <task> <run_dir>`, run your agent in `run_dir/workspace`, write
`answer.txt` (and `events.jsonl`), then call `repoevals grade <run_dir>`. The result is written to
`run_dir/grade.json`.

### Trace format

| event | fields |
|---|---|
| `{"kind": "command"}` | `text`, `rc`, `output`, `denied` |
| `{"kind": "read"}` / `{"kind": "edit"}` | `path` |
| `{"kind": "message"}` | `text` |
| `{"kind": "network"}` | `text` |
| `{"kind": "usage"}` | `input_tokens`, `output_tokens`, `cached_tokens`, `cost_usd` |

### `grade.json`

```json
{
  "schema": "repoevals.grade/v1",
  "task": "fix_var_ddof", "family": "fix", "grader": "hidden_tests",
  "agent": "claude-code", "model": "...", "arm": "guidance:raw", "repeat": 0,
  "status": "pass", "pass": true,
  "metrics": {"strict_pass": true, "diff_lines": 2},
  "details": {"tests": {"status": "passed", "passed": 4}},
  "process": {"n_commands": 12, "cost_usd": 0.41},
  "integrity": {"integrity_ok": true, "integrity_findings": []},
  "errors": []
}
```

## Harbor export

```bash
repoevals export harbor --out harbor-tasks/
```

Writes each task in the [Harbor](https://harborframework.com) task format: `task.toml`, `instruction.md`,
`environment/Dockerfile`, `solution/solve.sh` and a verifier `tests/test.sh` that runs `repoevals grade` and writes
the reward. Provide an environment image for your repository through `[environment].dockerfile`.
