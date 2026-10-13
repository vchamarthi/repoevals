# Getting started

This guide adopts repoevals in an existing repository: configure it, write a first set of tasks, verify them, run
an agent and wire everything into CI.

## 1. Install

```bash
pip install "repoevals @ git+https://github.com/vchamarthi/repoevals@v0.2.0"
repoevals --version
```

Install repoevals in the same environment where your project's tests run: graders call your test command. Agents
work in that environment too, so for builds that install into it (`pip install -e .`), run attempts one at a time,
or use per-run environments or containers before using `--jobs`.

## 2. Configure

```bash
cd your-project
repoevals init
```

`init` creates `repoevals.toml` and `.agent-evals/tasks/` (`init --preset dpnp` and friends start from a starter
configuration). The settings that matter most:

```toml
[repo]
id = "your-project"
tasks = ".agent-evals/tasks"

[policy]
test_globs = ["tests/**"]                  # which files are tests
library_globs = ["your_package/**"]        # which files are library code
protected_globs = []                       # files always restored before grading (skip lists, fixtures)

[test]
adapter = "pytest"                         # pytest | bazel | ctest | command
build = "pip install -e . -q"              # how a tree becomes testable; empty for pure Python
timeout_sec = 1800
```

Every option is described in [Configuration](configuration.md). Then run `repoevals doctor`, optionally with a test
target, to confirm git history, the test command and the agent CLIs work.

## 3. Write tasks

Start with 10–20 tasks taken from your project's real history. `repoevals author` automates most of the steps
below, and an AI agent can do the whole procedure for you — see [Creating tasks](authoring.md). In order of value per
hour of effort:

### Bug fixes from merged pull requests

1. Pick a merged fix that came with a test (`repoevals author mine`).
2. Scaffold it: `repoevals author fix <fix-sha>`. This sets `base` to the fix's parent commit, copies the test files
   the fix touched into `hidden/` (as of the fix; a modified test file replaces the agent's copy while grading), and
   saves the rest of the fix as `oracle.patch`.
3. Narrow `targets` to the tests that detect the bug, e.g. `tests/test_kmeans.py::test_empty_cluster_relocation`.
4. Write `instruction.md` as the original bug report: the symptom, a reproducer and the expected behaviour. Do not
   describe the fix.

```toml
version = "1.0"

[metadata]
family = "fix"
base = "3f2a91c^"
created = "2026-05-14"                     # when the fix became public
source = "https://github.com/your-org/your-project/pull/1234"

[grader]
type = "hidden_tests"
targets = ["tests/test_kmeans.py::test_empty_cluster_relocation"]
regression_targets = ["tests/test_kmeans.py"]
```

Alternatively, inject a bug into the current code with `setup.patch`; its reverse is used as the reference
solution.

### A seeded review and a clean control

Write a realistic change containing 5–8 problems of different kinds (correctness, numerical robustness, API,
hygiene), save it as `review.patch`, and describe each problem in `manifest.json`. Add 1–3 **decoys**: code that
looks wrong but follows your project's conventions. Then add a second review task with a clean change and an empty
manifest, which passes only when the agent raises no serious findings.

### A test-writing task

Choose a function without tests. Write 5–8 **mutants**, each a small patch introducing one realistic fault
(`mutants/*.patch`), and reference tests in `oracle_tests/` that detect all of them.

### A numerical task (numerical libraries)

Write a `probe.py` comparing your function with a trusted reference (NumPy, SciPy, mpmath, a closed form) on edge
cases: very large and very small values, NaN and Inf, empty and non-contiguous inputs, every supported dtype.

Full details for every task type: [Tasks](tasks.md). A working example of each is in
[`examples/minisci`](../examples/minisci).

## 4. Verify the tasks

```bash
repoevals validate --source .      # schema, files, instruction hygiene, patches apply at their base
repoevals check --repeats 3        # null run fails, reference passes 3 times, adversarial answers fail
```

Fix every `ERROR` and `PROBLEM` before running agents. `WARN` lines point at likely problems, such as an
instruction that mentions a commit hash or links to the fixing pull request.

Good tasks:

- describe the problem as a user would, never the solution;
- are graded on behaviour stated in the instruction, not on details of the reference solution;
- pass reliably with the reference solution — if `check --repeats 3` is not stable, fix or drop the task;
- carry a `created` date, so results can be separated by whether a model could have seen the solution.

## 5. Run agents

```bash
repoevals run --agent claude-code --repeats 5     # prints the runs directory
repoevals report <runs-dir>
```

To measure whether your agent guidance (`AGENTS.md`, `CLAUDE.md`, Copilot instructions) helps, run the same tasks
with and without it:

```bash
repoevals run --agent claude-code --arm guidance:raw --arm guidance:none --repeats 8
```

Plan for at least 8 repeats per task and arm when comparing configurations; smaller differences need more tasks or
repeats to be distinguishable from noise. The report states the confidence interval for every comparison.

## 6. Add CI

See [GitHub Action](github-action.md) for a ready-to-use workflow that validates and self-checks tasks on every pull
request, and optionally runs agents on a schedule.
