# Creating tasks

Good tasks come from your project's real history: bugs that were fixed with tests, pull requests that received
careful review, code that deserves better tests. repoevals helps you find them, scaffolds them as **drafts**, and can
hand the whole procedure to an AI agent. You stay in charge: drafts are excluded from agent runs until a human
approves them.

```
mine candidates ──► scaffold drafts ──► complete and verify ──► human review ──► approve
 (author mine)      (author fix, …)     (check --repeats 3)      (AUTHORING_REPORT)  (author approve)
```

## Option A: let an agent author the tasks

The [agent playbook](agent-playbook.md) is a complete, step-by-step procedure for an AI coding agent: set up the
repository, configure repoevals, mine candidates, scaffold and complete each task type, verify everything with
`repoevals check`, and write a report for you to review.

### Claude Code

Point Claude Code at the playbook and your repository:

```bash
claude "Create 12 repoevals tasks for https://github.com/your-org/your-project following \
https://github.com/vchamarthi/repoevals/blob/main/docs/agent-playbook.md"
```

Or install the playbook as a skill in your repository, so anyone working in it can ask for tasks:

```bash
repoevals author install                       # writes .claude/skills/repoevals-task-author/SKILL.md
claude "Use the repoevals-task-author skill to create 10 tasks for this repository"
```

### Codex

```bash
codex "$(repoevals author prompt --repo https://github.com/your-org/your-project --count 12)"
```

Or install it as a Codex custom prompt and invoke it with `/prompts:repoevals-author`:

```bash
repoevals author install --no-claude --codex   # writes ~/.codex/prompts/repoevals-author.md
```

### Any other agent

Print a self-contained prompt and give it to the agent:

```bash
repoevals author prompt --repo https://github.com/your-org/your-project --count 12 --families "fix, review"
repoevals author playbook > PLAYBOOK.md         # the full procedure, if the agent cannot open URLs
```

### What you get back

- `repoevals.toml`, configured for your project;
- `.agent-evals/tasks/*`, every task with `status = "draft"`, validated and passing `repoevals check --repeats 3`;
- `.agent-evals/AUTHORING_REPORT.md`: each task with its source and rationale, the candidates that were dropped and
  why, open questions, and a review checklist.

## Option B: author tasks yourself

### Find candidates

```bash
repoevals author mine --since 2024-01-01 --json candidates.json
```

```
  6.5  2026-05-14       #3742  lib   48 lines/2 files  tests 1  Fix empty-cluster relocation in KMeans (#3742)
  6.0  2026-04-02       #3690  lib   12 lines/1 files  tests 1  Handle NaN in abs for float16 (#3690)
```

Candidates are commits that changed library code and tests together, ranked by how likely they are to make good
fix tasks: bug-fix wording, a focused library change, and a meaningful test change.

### Scaffold a fix or feature task

```bash
repoevals author fix 3f2a91c --id fix_kmeans_empty_cluster
```

The draft contains:

| file | content |
|---|---|
| `task.toml` | base = the commit's parent, created date, source link, `hidden_tests` grader targeting the changed test files |
| `oracle.patch` | the non-test part of the commit |
| `hidden/` | the test files as of the commit |
| `instruction.md` | a template to replace with the problem statement |
| `AUTHOR_NOTES.md` | commit message, changed files and links, for the author only |

For projects evaluated against prebuilt release binaries (no compiler available), author tasks at the release
instead of the fix's parent:

```bash
repoevals author mine --release-base 0.20.0          # Python-only fixes after 0.20.0 that apply at 0.20.0
repoevals author fix <commit> --base 0.20.0 --extract-tests
```

`--extract-tests` hides only the test functions the fix added or changed, in a new test module, so newer test
helpers elsewhere in the original module do not break the task at the older base. Set `[repo].upstream` when
working in a fork, so release tags used as bases are fetched automatically.

Then write the instruction, narrow `targets` to the tests that detect the bug, add `regression_targets`, and run
`repoevals check fix_kmeans_empty_cluster --repeats 3`.

### Scaffold a review-replay task

Requires the GitHub CLI (`gh`), authenticated.

```bash
repoevals author review-replay 3182
```

This picks the revision of the pull request that received the most inline review comments, saves the change as
`review.patch` and the reviewers' comments as `gold.json`. With `--base <release>`, the reviewed change is
re-applied on that release and each comment's line is remapped; comments that cannot be mapped are listed under
`dropped`. Edit `gold.json` to keep only comments that ask for a
change.

### Scaffold a test-writing task

```bash
repoevals author mutants test_chebyshev minisci/distance.py --function chebyshev
# write reference tests in .agent-evals/tasks/test_chebyshev/oracle_tests/...
repoevals author prune-mutants test_chebyshev
```

`mutants` generates faults for Python code — flipped comparisons and arithmetic, swapped `min`/`max` and `and`/`or`,
removed `abs` and negations, shifted constants and slice bounds, negated conditions — and writes them as patches.
`prune-mutants` keeps only the mutants your reference tests detect; the rest are usually equivalent to the original.
For other languages, write mutant patches by hand.

### Seeded reviews, clean controls and numerical tasks

These are written by hand; see [Tasks](tasks.md) for the file formats and the [playbook](agent-playbook.md#phase-4--review-tasks)
for a procedure.

## Reviewing drafts

Before approving a task, check that:

- the instruction describes a problem a user could report, and does not reveal the solution;
- the hidden tests, manifest or gold comments check what the instruction asks for, and nothing else;
- `repoevals check <task> --repeats 3` is `OK`;
- the task runs on the hardware you will evaluate on.

```bash
repoevals list                          # shows draft / ready for every task
repoevals author approve fix_kmeans_empty_cluster review_replay_pr3182
```

`repoevals validate` warns about remaining drafts, and `repoevals run` skips them unless you pass
`--include-drafts`.
