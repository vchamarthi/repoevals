# Adopting agentexam in a repository

The repository owns **what** is measured (tasks, a short config). agentexam owns **how** (workspaces, graders,
integrity checks, statistics, CI).

## 1. Config

```bash
pip install "agentexam @ git+https://github.com/vchamarthi/agentexam"
cd your-repo && agentexam init
```

`agentexam init` writes `agentexam.toml` and an example task. Edit the four things that are repository-specific:

```toml
[test]
adapter = "pytest"                                   # pytest | bazel | ctest | command
build = "pip install -e . --no-build-isolation -q"   # how the tree becomes testable (empty for pure Python)

[policy]
test_globs = ["mypkg/tests/**"]                      # what counts as a test
library_globs = ["mypkg/**"]                         # what counts as library code
protected_globs = ["mypkg/tests/skipped_tests*.tbl"] # always reset before grading (skip lists, fixtures)
```

See [config.md](config.md) for every key.

## 2. Tasks — start with 10–20 from real history

Good first tasks, in order of value per hour of effort:

1. **Fixes mined from merged PRs** (`fix`, `hidden_tests`). Base = the PR's parent commit. Hidden tests = the tests
   the PR added (copy them to `hidden/`). Reference = the PR's library diff saved as `oracle.patch`.
   Write the instruction as the *issue* (symptom, reproducer, expected behaviour), never the fix.
2. **A seeded review and a clean control** (`review_seeded`). Plant 5–8 defects of different classes in a realistic
   change, add 1–3 *decoys* (code that looks wrong but is your project's idiom), and add one clean change.
3. **One test-writing task** (`mutation`) for a function with no tests: 5–8 mutants as patches, reference tests
   that kill all of them.
4. For numerical libraries, **one `numeric` task**: a probe comparing against NumPy/SciPy/mpmath on edge cases
   (huge/tiny values, NaN/Inf, empty, non-contiguous, every supported dtype).

Then: `agentexam validate --source .` and `agentexam check --repeats 3` until every task is `OK`.

Rules of thumb (from the Agentic Benchmark Checklist and the oneDAL pilot):

- Fill `[metadata].created` with the date the fix became public, so results can be split by model training cutoff.
- Hidden tests should test **behaviour stated in the instruction**, not an implementation detail of the oracle.
- If the reference solution needs >3 attempts to pass reliably, the task is flaky: fix it or drop it.
- A pilot with ~150 runs at 8+ repeats per (task, arm) is the minimum to see a guidance effect of ~10 points.

## 3. CI

```yaml
# .github/workflows/agent-evals.yml
on:
  pull_request:
    paths: [".agent-evals/**", "agentexam.toml", "**/AGENTS.md", ".github/instructions/**"]
  schedule: [{cron: "17 3 * * 1"}]          # weekly full self-check
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}               # tasks reference base commits
      - uses: vchamarthi/agentexam@v0
        with: {command: validate}
      - uses: vchamarthi/agentexam@v0
        with: {command: guidance-lint}
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - run: ./ci/setup-env.sh               # whatever makes your tests runnable
      - uses: vchamarthi/agentexam@v0
        with: {command: check, changed-only: "true", repeats: "2"}
```

No model and no secret is used, so this is safe on pull requests from forks. Agent runs (`command: run`) need an
API key and belong in a manually triggered or scheduled workflow, ideally inside a container with no network
(`agentexam run --docker <image>`).

## 4. Running agents

```bash
agentexam run --agent claude-code --model <model-id> --arm guidance:raw --arm guidance:none --repeats 8
agentexam run --agent codex --repeats 8 family:fix
agentexam run --agent "cmd:my-agent --prompt-file {prompt_file}" --repeats 3
agentexam report runs/<stamp>
```

The report gives per-arm pass rates with task-clustered 95% CIs, paired deltas between arms (same tasks) with a
sign-flip p-value, pass@k and pass^k per task, cost and tokens, and every run flagged by the integrity checks — read
those transcripts before trusting a number.
