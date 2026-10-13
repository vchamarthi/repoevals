# Tasks

A task is a directory inside your tasks folder. Its name is the task id.

## Directory layout

| file | used by | purpose |
|---|---|---|
| `task.toml` | all | task definition (required) |
| `instruction.md` | all | the prompt the agent receives (required, or `prompt = "..."` in `task.toml`) |
| `setup.patch` | all | applied to the workspace before the agent starts, e.g. to re-introduce a bug |
| `oracle.patch` | code tasks | reference solution. Defaults to the reverse of `setup.patch` |
| `oracle.sh` | `command` | reference solution as a script, run in the workspace |
| `hidden/` | `hidden_tests`, `numeric`, `perf` | files copied onto the workspace only while grading. A `.oracle` suffix is dropped, so `BUILD.oracle` becomes `BUILD` |
| `review.patch` | review | the change under review, committed on top of the base |
| `manifest.json` | `review_seeded` | known problems (`seeds`) and acceptable look-alikes (`decoys`) |
| `gold.json` | `review_replay` | review comments from a real pull request |
| `oracle_answer.json` | review, `mutation`, `command` | the reference final answer |
| `mutants/*.patch` | `mutation` | one fault per patch |
| `oracle_tests/` | `mutation` | reference tests, copied onto the workspace |
| `probe.py` | `numeric` | test cases, candidate call and reference implementation |
| `bench.py` | `perf` | benchmark printing `{"seconds": <float>}` |
| `AUTHOR_NOTES.md` | authoring | notes written by `repoevals author`; informational |

None of these files are visible to the agent.

## `task.toml`

```toml
version = "1.0"

[metadata]
family = "fix"
base = "a492e703^"
created = "2026-05-14"
source = "https://github.com/your-org/your-project/pull/3742"
difficulty = "medium"
tags = ["kmeans"]

[environment]
requires = { cpus = 4, cpu_flags = ["avx512f"], cpu_vendors = ["GenuineIntel"], devices = ["gpu"] }

[agent]
timeout_sec = 3600

[verifier]
timeout_sec = 1800

[grader]
type = "hidden_tests"
gate = ["strict_pass"]
```

| key | description |
|---|---|
| `metadata.family` | `build`, `fix`, `feature`, `test`, `review`, `numeric`, `perf`, `port`, `docs` or `custom`. `port`, `docs` and `custom` have no dedicated grader: use `hidden_tests`, `command` or your own |
| `metadata.status` | `ready` (default) or `draft`. Drafts are skipped by `run` unless `--include-drafts` is given |
| `metadata.base` | git revision the workspace is created from |
| `metadata.created` | date the solution became public; carried into every result |
| `metadata.source`, `difficulty`, `tags` | informational; `tags` can be used to select tasks (`tag:<name>`) |
| `environment.requires` | hardware the task needs. On hosts that do not meet it the task is `skipped` |
| `agent.timeout_sec` | time limit for the agent (default 3600) |
| `verifier.timeout_sec` | time limit for grading steps (default 1800) |
| `grader.type` | the grader, see below |
| `grader.gate` | conditions for a pass: metric names that must be true, or comparisons such as `"recall>=0.6"` or `"decoys_flagged<=0"` (a list metric compares by its length) |
| `grader.integrity_gate` | fail the task on any integrity violation |
| `grader.adapter`, `build`, `test_command`, `test_env` | override the repository's test settings for this task |

## Graders

### `hidden_tests` — fixes and features

```toml
[grader]
type = "hidden_tests"
targets = ["tests/test_linalg.py::test_svd_fortran_order"]
regression_targets = ["tests/test_linalg.py"]
root_cause_files = ["dpnp/linalg/dpnp_utils_linalg.py"]
max_diff_lines = 400
```

Grading restores protected files, existing tests the agent modified, and test infrastructure (`conftest.py`,
`pytest.ini`, `tox.ini`); copies `hidden/` onto the tree; builds; runs `targets`, then `regression_targets`; and
finally restores the agent's tree exactly.

| option | default | description |
|---|---|---|
| `targets` | required | tests that must pass |
| `regression_targets` | `[]` | existing tests that must keep passing |
| `root_cause_files` | `[]` | reported as `root_cause_touched` |
| `max_diff_lines` | none | reported as `diff_ok` |
| `protected` | `[]` | extra globs restored before grading |
| `allow_test_edits` | `false` | keep the agent's edits to existing tests |
| `allow_hidden_skips` | `false` | allow skipped hidden tests |

Metrics: `strict_pass` (default gate), `hidden_pass`, `regression_pass`, `build_ok`, `files_changed`,
`diff_lines`, `touched_tests`, `format_ok`.

### `command` — builds and setup

```toml
[grader]
type = "command"
commands = ["python -c 'import dpctl; dpctl.lsplatform()'"]
artifacts = ["build/lib/libfoo.so"]
artifact_kind = "elf"
require_clean_tree = true
allowed_globs = ["build/**"]
```

`{answer.<key>}` in commands and artifacts is replaced by a value from the JSON object at the end of the agent's
answer (shell-quoted in commands). Default gate: `commands_ok` and `artifacts_ok`, plus `clean_tree` when
`require_clean_tree` is set. Artifacts must be at least `artifact_min_bytes` (default 1024) and, with
`artifact_kind`, structurally valid: `elf` (executable or shared object header), `zip`, or `wheel` (a valid archive
with `*.dist-info/WHEEL`). Prefer commands that exercise the artifact, such as importing the built module.

### `mutation` — test writing

```toml
[grader]
type = "mutation"
min_kill = 0.8
oracle_min_kill = 1.0
```

Every non-test change is reverted, the agent's tests must pass on the real code, then each mutant is applied in
turn. A mutant is **killed** when the tests fail, **survives** when they pass, and is **invalid** (excluded) when the
code does not build or the tests time out (set `timeout_kills = true` to count timeouts as kills). A pass needs
`kill_rate >= min_kill`. Tests that read source files, hashes or patches instead of exercising behaviour are flagged
as `meta_testing`.

The tests are taken from `targets` in the task, else from the agent's answer
(`{"targets": ["tests/test_x.py"]}`, test files only), else from the test files the agent changed.
`repoevals check` requires the reference tests to kill at least `oracle_min_kill` of the mutants.

### `review_seeded` — review with known problems

The agent's answer must end with a JSON array of findings:

```json
[{"file": "pkg/stats.py", "line": 42, "severity": "blocker", "rule": "overflow", "message": "exp overflows for large inputs"}]
```

`manifest.json`:

```json
{
  "seeds": [
    {"id": "S1", "class": "numerical", "file": "pkg/stats.py", "lines": [36, 37], "kw": "overflow|stab"}
  ],
  "decoys": [
    {"id": "D1", "file": "pkg/stats.py", "lines": [34], "kw": "float64|dtype"}
  ]
}
```

Findings without a message earn no credit. A finding matches a seed when it names the same file and either its
text matches the `kw` regular expression and
its line is within `kw_line_tol` (15) lines, or its line is within `line_tol` (1) lines. Each finding counts for at
most one seed; a finding matching several seeds counts only for the nearest. A seed with `"file": "*"` matches any
file and requires the keyword.

| option | default | description |
|---|---|---|
| `manifest` | `manifest.json` | seeds and decoys |
| `review_patch` | `review.patch` | the change under review |
| `review_title` | `"Change under review"` | commit message of the change |
| `max_findings` | 2 × seeds + 5 | findings beyond this are ignored and `capped` is set |
| `max_findings_clean` | 2 | clean controls: maximum findings allowed |

Metrics: `recall`, `precision`, `f1`, `seeds_hit`, `decoys_flagged`, `recall_by_class`, `n_findings`. Default gate:
`recall>=0.5`, `precision>=0.5`, `decoys_flagged<=0`. A manifest without seeds is a **clean control**: it passes
when there are no `blocker`/`major` findings and at most `max_findings_clean` findings in total.

### `review_replay` — review a past pull request

`gold.json` holds the human review comments: `{"threads": [{"file": "...", "line": 120, "body": "..."}]}`.
A finding matches a thread in the same file within `line_tol` (5) lines, one-to-one. Metrics: `recall`,
`precision`, `f1`. Without a `gate` the task is scored, not passed or failed.

### `numeric` — numerical accuracy

```toml
[grader]
type = "numeric"
probe = "probe.py"
python = "python3"
min_case_pass = 1.0
targets = ["tests/test_basic.py"]

[grader.tolerances]
float32 = [1e-5, 1e-6]     # [rtol, atol]
float64 = [1e-12, 1e-14]
```

```python
# probe.py
import numpy as np

CASES = [{"id": "large", "x": [1000.0, 1000.0]}, {"id": "empty", "x": []}]

def candidate(case):
    import your_package                    # import the code under test inside this function
    return your_package.logsumexp(case["x"])

def reference(case):
    x = np.asarray(case["x"], dtype=np.float64)
    ...
```

The candidate, the reference and the comparison run in separate processes; the code under test cannot affect the
reference or the comparison. Each case passes when shape and dtype match, NaN and Inf appear in the same positions,
and `|got - want| <= atol + rtol * |want|`. Per-case `rtol`, `atol` and `check_dtype` override the defaults. Define
`compare(case, got, want) -> {"ok": bool, ...}` in the probe for custom comparisons. A candidate that raises or
crashes fails its cases.

Metrics: `case_pass_rate`, `n_cases_ok`, `max_ulp`, and per-case errors in `cases`.

### `perf` — performance

```toml
[grader]
type = "perf"
bench = "python bench.py"
repeats = 7
warmup = 1
min_speedup = 1.2
targets = ["tests/test_core.py"]
```

Correctness first: `targets` must pass. The starting revision and the agent's tree are then benchmarked in pairs, in
random order within each pair, each with its own tree on `PYTHONPATH`. repoevals measures the wall-clock time of each
run itself (set `trust_bench_output = true` to use the `{"seconds": …}` the benchmark prints instead). The task
passes when the lower bound of the 95% confidence interval of the median per-pair speedup is at least `min_speedup`.
For compiled extensions, build in place (`build_ext --inplace`) rather than with an editable install.

Metrics: `speedup`, `speedup_ci`, `noise_cv_base`, and raw timings.

## Selecting tasks

Commands that take tasks accept ids (`fix_var_ddof`), prefixes (`fix_*`), families (`family:review`) and tags
(`tag:kmeans`). With no selection, all tasks are used.
