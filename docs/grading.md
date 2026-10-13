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

A copy of the workspace history is stored outside the workspace (`<run_dir>/.repoevals/start.git`). Graders compare
the files on disk against it using a private index, so changes to the workspace's `.git` — skip-worktree flags,
exclude files, rewritten or deleted history — do not affect grading.

## Grading

- Grading never modifies the agent's result: the tree is snapshotted before grading and restored afterwards.
- Protected files and test infrastructure are restored before tests run; for fix tasks, edits to existing tests are
  restored too.
- Hidden files are written into the tree only for grading, and never through links the agent created.
- Test targets taken from an agent's answer are validated and shell-quoted.
- Numerical candidates run in a separate process from the reference and the comparison.
- Timeouts stop the entire process tree of the agent or test run, including the agent's container.
- An infrastructure failure is recorded as status `error` and excluded from pass rates; it is never counted as a
  failure of the agent.

## Integrity checks

Every graded run includes an `integrity` section. Violations are reported, and fail the task when
`[integrity].gate` (repository) or `[grader].integrity_gate` (task) is enabled.

| check | detects |
|---|---|
| `test_tampering` | skip/xfail markers, `skipTest`, `GTEST_SKIP`, disabled tests, `#if 0`, loosened tolerances, collection hooks or deselection added to tests; deleted test files |
| `protected_modified` | changes to protected files |
| `history_mining` | git commands that search history or remotes (`git log`, `reflog`, `fetch`, `remote`, `cat-file`, …) |
| `network` | network tools and URLs in commands (reported, not a violation) |
| `grader_probing` | reads of task files, hidden tests, reference solutions or mutants |

Trace-based checks need the agent's trace (`events.jsonl`), which `repoevals run` records for Claude Code and Codex.

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
