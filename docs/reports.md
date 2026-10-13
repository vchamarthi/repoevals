# Reports and statistics

```bash
repoevals report runs/20261013-091500
```

The report groups runs by **configuration**: agent, model and arm (for example `claude-code|<model>|guidance:none`).

## Output files

| file | contents |
|---|---|
| `summary.md` | human-readable report, also written to the GitHub job summary |
| `results.json` | all aggregated numbers |
| `results.jsonl` | one record per graded run: task, family, configuration, status, pass, created date, metrics (`m_*`), process data (`p_*`), integrity |

## Metrics

| metric | meaning |
|---|---|
| pass rate | mean over tasks of each task's share of passing attempts |
| 95% CI | confidence interval from resampling tasks, then attempts within each task |
| pass@k | probability that at least one of k attempts passes (unbiased estimator) |
| pass^k | probability that all k attempts pass: a measure of reliability |
| Δ pass rate | paired difference between two configurations on the tasks both ran |
| p (sign-flip) | exact sign-flip permutation test of the paired difference |
| better / worse | number of tasks on which the second configuration did better or worse |
| cost, tokens | totals reported by the agent CLI |

Runs with status `skipped` or `error` are excluded from pass rates and listed in the status counts. Tasks without a
pass/fail gate (status `scored`) contribute their scores (`f1`, `recall`, `kill_rate`, `speedup`,
`case_pass_rate`) to the per-configuration score table.

## Interpreting results

- Compare configurations only on the same tasks; the paired comparison does this for you.
- A confidence interval that includes zero means the difference is not established; add tasks or repeats.
- Read the transcripts of every run listed under **Integrity flags** before trusting a number.
- A task that every configuration passes, or none does, adds no information to a comparison; review it.
- Use `created` dates to compare results on tasks published before and after a model's training cutoff.

## Process data

Each `grade.json` includes `process`, derived from the agent's trace:

| field | meaning |
|---|---|
| `tool_calls`, `n_commands`, `n_command_fail` | agent activity |
| `n_build_cmds`, `n_build_fail` | commands matching the task's `build_regex` |
| `n_edits` | file edits |
| `guidance_read` | guidance files the agent actually opened |
| `input_tokens`, `output_tokens`, `cost_usd` | usage reported by the agent CLI |
