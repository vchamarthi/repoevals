# Grading and integrity

repoevals is built so that a score reflects what the agent did, and so that the same run always gets the same
score.

## Workspace isolation

For every attempt, `prepare` creates a fresh workspace:

1. The repository tree at the task's base revision is written out.
2. The tasks directory, repository graders, `.agent-evals/`, `repoevals.toml` and `[repo].hide` paths are removed.
3. `setup.patch` is applied, then the arm (guidance files kept, removed or replaced).
4. The result is committed as a single root commit. Review tasks add one commit with the change under review.
5. Reflogs are expired and a leak check fails preparation if any other commit, branch, tag, remote or stash exists.

The agent therefore cannot recover the upstream solution from git history, and never sees hidden tests, reference
solutions, mutants or manifests.

A copy of the workspace history is stored outside the workspace (`<run_dir>/.repoevals/start.git`), together with a
copy of the task (`<run_dir>/.repoevals/tasks/`). Graders compare the files on disk against that history using a
private index, so changes to the workspace's `.git` — skip-worktree flags, exclude files, rewritten or deleted
history — do not affect grading, and regrading an attempt later uses the task exactly as it was when the attempt ran.

## Isolation and threat model

| mode | what the agent can reach | use for |
|---|---|---|
| local (default) | the workspace, plus anything your user account can read: other run directories, the source checkout, task files | trusted agents, development, quick comparisons |
| `--docker` | the workspace and the prompt only; credentials listed in `env_passthrough` | results you publish, untrusted agents |

repoevals reduces local-mode exposure — run directories are kept outside the repository, credentials are removed
from the agent's environment, and reads of the source checkout or task files are flagged — but a local agent runs
with your permissions, and trace-based checks only see what the agent's trace records. Treat integrity checks as
evidence to review, and use a container where results must be trustworthy against a determined agent.

Grading always runs on the host and executes code the agent wrote (builds, tests, benchmarks). It never receives
your credentials, and every step has a time limit.

### Known limitations

These follow from running the code under evaluation, and cannot be closed by repoevals alone:

- **Code under test shares a process with the test runner.** A malicious agent can make its code write a forged
  pytest report. Integrity checks flag many tricks (shadowed modules, skips, edited tests) but not this one; for
  results that must be trustworthy against a determined agent, run grading in a locked-down container that mounts
  only the candidate workspace.
- **Grading runs on the host or in the container you configure** (`[environment].exec`), with whatever that
  environment can read. `--docker` isolates only the agent. Imports during grading can read paths the grading
  environment exposes; keep credentials and the source checkout out of it.
- **A local agent can read its run directory's siblings** (`.repoevals/`, shims). Use `--docker` for untrusted
  agents.
- **Module-level data in `probe.py` is visible to the code under test** (only the `reference` and `compare` functions
  are hidden). Keep expected values and reference helpers inside `reference()`.

## Grading

- Grading never modifies the agent's result: the tree is snapshotted before grading and restored afterwards.
- Protected files and test infrastructure are restored before tests run; for fix tasks, edits to existing tests are
  restored too.
- Hidden files are written into the tree only for grading, and never through links the agent created.
- Test targets taken from an agent's answer are validated and shell-quoted.
- Files that would shadow the test runner or hook interpreter start-up (`pytest.py`, `sitecustomize.py`, `*.pth`, …)
  are removed before tests run.
- Numerical candidates run in a separate process that sees only the test cases, never the reference or the
  comparison.
- Benchmarks are timed by repoevals, not by output of the benchmarked code.
- Timeouts stop the entire process tree of the agent or test run, including the agent's container.
- An infrastructure failure is recorded as status `error` and excluded from pass rates; it is never counted as a
  failure of the agent.

## Integrity checks

Every graded run includes an `integrity` section. Violations are reported, and fail the task when
`[integrity].gate` (repository) or `[grader].integrity_gate` (task) is enabled.

| check | detects |
|---|---|
| `test_tampering` | in existing test files (new test files the agent adds are its own): skip/xfail markers, `skipTest`, `GTEST_SKIP`, disabled tests, `#if 0`, loosened tolerances, collection hooks or deselection added to tests; deleted test files |
| `protected_modified` | changes to protected files |
| `history_mining` | git commands that reach outside the workspace (`fetch`, `pull`, `remote`, `clone`, `-C <other dir>`, `--git-dir`). Local `git log`/`show` are not flagged: the workspace has a single commit |
| `network` | network tools and URLs in commands (reported, not a violation) |
| `grader_probing` | reads of task files, hidden tests, reference solutions or mutants |
| `source_access` | reads, edits or commands that touch the source checkout outside the workspace |
| `meta_testing` | tests (in test-writing tasks) that inspect source files, hashes or patches instead of behaviour |

Trace-based checks need the agent's trace (`events.jsonl`), which `repoevals run` records for Claude Code and Codex;
results of `cmd:` agents are marked `trace: none`. Each graded run also stores the agent's changes as `agent.diff`.

## Task self-check

`repoevals check` verifies each task before it is used:

- the untouched workspace fails — otherwise the task measures nothing;
- the reference solution passes on every repeat — otherwise the task is broken or flaky;
- reference tests kill the required share of mutants — otherwise mutants are equivalent or invalid;
- one finding on every changed line fails review tasks — otherwise flooding findings would pass.

Run it in CI on every change to tasks.

## Determinism

Graders use no language model. Pin your environment (dependency versions, thread counts, random seeds) so tests
behave the same on every run; `check --repeats N` shows whether they do.
