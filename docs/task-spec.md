# Task specification (heldout task format v1)

One directory per task under `[repo].tasks`. The layout is a superset of the
[Harbor](https://harborframework.com) task directory (`task.toml` + `instruction.md`), and `heldout export harbor`
produces a Harbor-native copy.

```
<task_id>/
  task.toml            required
  instruction.md       required (or prompt = "..." in task.toml) — what the agent sees, nothing else
  setup.patch          applied before the agent starts (re-introduces a bug, removes a feature)
  oracle.patch         reference solution (default: reverse of setup.patch)
  oracle.sh            reference solution as a script (command tasks)
  hidden/              overlaid on the agent's tree only during grading; `X.oracle` is written as `X`
  review.patch         review tasks: the change under review, committed on top of the base
  manifest.json        review_seeded: {"seeds": [...], "decoys": [...]}
  gold.json            review_replay: {"threads": [{"file", "line", "body"}]}
  oracle_answer.json   reference final answer (review findings, mutation targets)
  mutants/*.patch      mutation: one fault per patch
  oracle_tests/        mutation: reference tests (overlaid like hidden/)
  probe.py             numeric: CASES, candidate(case), reference(case) [, compare(case, got, want)]
  bench.py             perf: prints {"seconds": float}
```

## task.toml

```toml
version = "1.0"

[metadata]
family = "fix"                 # build | fix | feature | test | review | numeric | perf | port | docs | custom
base = "a492e703^"             # any git revision in the source repository
created = "2026-05-14"         # date the solution became public (contamination control)
source = "https://github.com/org/repo/pull/3742"
difficulty = "medium"          # easy | medium | hard (author's estimate)
tags = ["kmeans", "cpu"]

[environment]
requires = { cpus = 4, cpu_flags = ["avx512f"], cpu_vendors = ["GenuineIntel"], devices = ["gpu"] }
# unmet requirements -> status "skipped"; devices are matched against $HELDOUT_DEVICES (default "cpu")

[agent]
timeout_sec = 3600

[verifier]
timeout_sec = 1800

[grader]
type = "hidden_tests"          # see below
gate = ["strict_pass"]         # metric names (truthy) or comparisons: "recall>=0.6", "decoys_flagged<=0"
integrity_gate = false         # true: an integrity violation fails this task
# adapter overrides for this task only:
# adapter = "pytest"  build = "..."  test_command = "..."  test_env = { KEY = "value" }
```

## Graders

| type | keys | pass |
|---|---|---|
| `hidden_tests` | `targets`, `regression_targets`, `root_cause_files`, `max_diff_lines`, `protected`, `allow_test_edits`, `hidden_dir` | gate (default `strict_pass` = targets and regression targets pass) |
| `command` | `commands`, `artifacts`, `artifact_kind` (`elf`/`wheel`/`zip`), `require_clean_tree`, `allowed_globs` | `commands_ok` and `artifacts_ok` (+ `clean_tree`) |
| `mutation` | `targets` (else answer JSON `{"targets": [...]}`, else changed test files), `min_kill`, `oracle_min_kill`, `mutants_dir`, `rebuild_per_mutant` | baseline passes and kill rate ≥ `min_kill` |
| `review_seeded` | `manifest`, `review_patch`, `review_title`, `max_findings`, `max_findings_clean`, `line_tol`, `kw_line_tol` | gate (default `recall>=0.5`, `precision>=0.5`, `decoys_flagged<=0`); no seeds = clean control |
| `review_replay` | `gold`, `review_patch`, `line_tol`, `max_findings` | gate, else score-only (`pass: null`) |
| `numeric` | `probe`, `python`, `tolerances` (`{dtype = [rtol, atol]}`), `min_case_pass`, `targets`, `pythonpath_workspace` | all (or `min_case_pass`) cases within tolerance and targets pass |
| `perf` | `bench`, `repeats`, `warmup`, `min_speedup`, `targets`, `bench_timeout_sec` | correctness, then speedup CI lower bound ≥ `min_speedup` |

### Review answer format

The agent's final message must end with a fenced JSON array (or `{"findings": [...]}`):

```json
[{"file": "pkg/mod.py", "line": 42, "severity": "blocker|major|minor", "rule": "short-name", "message": "one sentence"}]
```

### Seeds and decoys

```json
{"seeds":  [{"id": "S1", "class": "numerical", "file": "pkg/stats.py", "lines": [36, 37], "kw": "overflow|stab"}],
 "decoys": [{"id": "D1", "file": "pkg/stats.py", "lines": [34], "kw": "float64|dtype"}]}
```

A finding matches a seed if it is in the same file and its rule/message matches `kw` within `kw_line_tol` lines,
or its line is within `line_tol`; `"file": "*"` seeds require the keyword. Each finding credits at most one seed.

## Run directory (produced by `heldout prepare` / `heldout run`)

```
run_dir/
  workspace/       the agent's tree (one root commit; review tasks: + the change commit)
  instruction.md   the prompt
  task.json        resolved task
  meta.json        start_sha, base_sha, arm, agent, model, repeat, timings
  events.jsonl     normalized trace: command / read / edit / message / usage events
  answer.txt       the agent's final message
  grade.json       heldout.grade/v1
```

Any external harness can produce `workspace/`, `answer.txt`, `events.jsonl` and `meta.json` and call
`heldout grade <run_dir>`; heldout does not need to run the agent itself.
