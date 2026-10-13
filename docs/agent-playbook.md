# repoevals task-authoring playbook

You are an AI coding agent creating **repoevals** evaluation tasks for a software repository. Follow this playbook
step by step. A human maintainer will review everything you produce, so be precise, verify every claim by running
it, and record what you could not do.

## Inputs

You will be given:

- **REPO** — a repository URL or a local path (required);
- **COUNT** — how many tasks to deliver (default 12);
- **FAMILIES** — the task mix (default: mostly `fix`, plus `review`, `test`, and `numeric` for numerical code);
- optional focus areas, constraints, or a working directory.

## Deliverables

1. `repoevals.toml` configured for the repository.
2. `.agent-evals/tasks/<task_id>/` for every task, each with `status = "draft"`.
3. `repoevals validate --source .` reporting **0 errors**.
4. `repoevals check --repeats 3` reporting **OK** for every task you keep.
5. `.agent-evals/AUTHORING_REPORT.md` following the template at the end of this playbook.

## Ground rules

- Never invent facts. Every task comes from the repository's real code and history, or from a change you wrote and
  verified. Every claim in an instruction must be true at the task's base revision.
- Never weaken or delete the project's existing tests, and do not change the project's code outside
  `.agent-evals/` and `repoevals.toml`.
- Do not push, open pull requests, or comment on issues unless the human asked you to.
- Leave every task as `status = "draft"`. Approval is the human's decision.
- Prefer fewer, solid tasks over many weak ones. Drop a task rather than ship one that fails `repoevals check`.
- Keep a running log of decisions and dropped candidates; you will need it for the report.
- If something blocks you (the project does not build, tests need hardware you lack, `gh` is not authenticated),
  stop that line of work, record it in the report, and continue with what is possible.

## Phase 0 — Set up

1. Get the repository with full history. For a URL: `git clone <REPO> && cd <name>`. Do not use `--depth`.
2. Create an isolated environment and install the project for development, following its CI workflows
   (`.github/workflows/*`), `CONTRIBUTING.md`, or `README.md`. Prefer the exact commands CI uses.
3. Run a small part of the project's test suite to confirm the environment works. Note the exact command.
4. Install repoevals in the same environment and confirm it runs:

   ```bash
   pip install "repoevals @ git+https://github.com/vchamarthi/repoevals"
   repoevals --version
   ```

5. Check whether `gh auth status` succeeds. Review-replay tasks need it; everything else works without it.

## Phase 1 — Configure

1. Run `repoevals init` (skip if `repoevals.toml` exists; then review it instead). If the project has a starter
   configuration, use it: `repoevals init --preset <name>` (`dpnp`, `dpctl`, `mkl_fft`, `mkl_random`, `mkl_umath`,
   `scikit-learn-intelex`, `xgboost`, `onedal`).
2. Fill in `repoevals.toml` from what you learned in Phase 0:
   - `[test].adapter`: `pytest` for Python projects, `ctest`, `bazel`, or `command` otherwise;
   - `[environment].setup`: the command that makes a fresh checkout testable, run once per workspace before the
     agent starts (empty for pure Python);
   - `[test].build` and `[test].rebuild_globs`: how to rebuild after a change, and which changed files need it
     (compiled sources), so Python-only changes do not trigger a full rebuild;
   - `[test].command`: only if the adapter's default does not work, keeping `{targets}`;
   - `[test].env`: variables CI sets (device selectors, thread counts, feature flags);
   - `[policy].test_globs` and `library_globs`: where tests and library code live;
   - `[policy].protected_globs`: skip lists, deselect lists, expected-failure lists, golden files;
   - `[guidance].globs`: the project's agent guidance files, if any.
3. Verify: `repoevals doctor <a fast test target>` reports no `FAIL` lines.

The full reference is `docs/configuration.md` in the repoevals repository.

## Phase 2 — Find candidates

```bash
repoevals author mine --limit 1000 --json .agent-evals/candidates.json
```

This ranks commits that changed library code and tests together. Also look at merged pull requests with
substantive review discussion (`gh pr list --state merged --limit 200`) and at modules with weak test coverage.

Choose candidates against these criteria:

| prefer | avoid |
|---|---|
| a real bug with a clear symptom | refactors, dependency bumps, formatting, documentation-only changes |
| library change of roughly 3–300 lines | changes touching many subsystems or generated files |
| tests that fail before the change and pass after | tests that only pass because of new APIs the instruction would have to name |
| behaviour describable without revealing the fix | fixes whose only description is the fix itself |
| runs on available hardware (usually CPU) | GPU-only or multi-node behaviour, unless the human asked for it |
| a spread of modules and difficulty | five variants of the same bug |

Aim for this mix unless told otherwise: about 60% `fix`/`feature`, 20% `review` (at least one seeded review, one
clean control, and replays if `gh` works), 10–20% `test`, and `numeric` tasks for numerical libraries.

## Phase 3 — Fix and feature tasks

For each chosen commit:

1. Scaffold the draft:

   ```bash
   repoevals author fix <commit> --id fix_<area>_<short_name>          # or --family feature
   ```

   This creates `task.toml` (base = the commit's parent), `oracle.patch` (the non-test part of the commit),
   `hidden/` (the test files as of the commit), `instruction.md` (a template) and `AUTHOR_NOTES.md`.

2. Read `AUTHOR_NOTES.md`, the commit, the linked pull request and issue (`gh pr view <n>`, `gh issue view <n>`).

3. **Write `instruction.md`.** Replace the whole template. Write it the way a user would report the problem:
   - the observed behaviour and the expected behaviour;
   - a minimal reproducer (code snippet or command) that runs at the base revision;
   - constraints a user would state ("must match NumPy", "must not change the public API").

   Never mention the fix, the functions or lines it changes, the fixing commit or pull request, or text such as
   "FIXED in". Do not copy an issue verbatim if it contains the solution. End with "Do not weaken or delete existing
   tests."

4. **Narrow `targets`** to the tests that fail before the fix and pass after it. With pytest, use node ids
   (`tests/test_x.py::test_case`). Confirm both directions:

   ```bash
   repoevals check <task_id>
   ```

   `null=fail` means the hidden tests detect the bug; `oracle=['pass']` means the reference fix passes.

5. **Add `regression_targets`**: existing tests of the same component that must keep passing (test files or
   directories, not the whole suite if it is slow).

6. Set `difficulty`, `tags`, and `[environment].requires` if the task needs specific hardware.

7. Run `repoevals check <task_id> --repeats 3`. If it is not `OK`:

   | problem | likely cause and fix |
   |---|---|
   | untouched workspace passes | hidden tests do not exercise the bug: pick other tests or drop the task |
   | reference does not pass | `oracle.patch` misses generated or build files, or targets include unrelated failing tests |
   | flaky across repeats | nondeterminism (threads, random seeds, timing): pin it in `[test].env` or drop the task |
   | build error | `[test].build` is wrong for this base revision: fix it or drop the task |

## Phase 4 — Review tasks

### Review replay (needs `gh`)

1. Pick merged pull requests with at least three substantive inline review comments from maintainers.
2. Scaffold: `repoevals author review-replay <pr_number>`.
3. Edit `gold.json`: keep only threads that ask for a change a good reviewer should request. Remove praise,
   questions answered in the thread, and pure style nits. Keep at least three threads, or drop the task.

### Seeded review

1. Write a realistic change for the project (a new helper, a small feature) that contains 5–8 problems of different
   kinds: correctness, numerical robustness, API or behaviour, tests, hygiene, project conventions.
2. Add 1–3 **decoys**: code that looks suspicious but follows the project's documented conventions (read its
   `AGENTS.md`, `CONTRIBUTING.md` and similar code) and must not be flagged.
3. Create the task directory by hand:
   - `review.patch` — the change, as `git diff` output against the base;
   - `manifest.json` — `seeds` (`id`, `class`, `file`, `lines` in the new file, `kw` regex of words a correct
     finding would use) and `decoys` (`id`, `file`, `lines`, `kw`);
   - `oracle_answer.json` — one correct finding per seed;
   - `instruction.md` — the standard review instruction (copy it from a replay task or the repoevals docs);
   - `task.toml` — `family = "review"`, `status = "draft"`, `[grader] type = "review_seeded"`,
     `gate = ["recall>=0.6", "precision>=0.5", "decoys_flagged<=0"]`.
4. Make the `kw` patterns broad enough to accept different wordings of the same problem, and check line numbers
   against the new version of each file.

### Clean control

Write a small, correct change in the project's style (a docstring improvement plus a test). Use an empty manifest
(`{"seeds": [], "decoys": []}`) and `oracle_answer.json` containing `[]`.

Run `repoevals check <task_id>` for every review task. A review task must pass with its oracle answer and must fail
the automatic "one finding per changed line" probe.

## Phase 5 — Test-writing tasks

1. Find a function with little or no test coverage whose behaviour is well defined.
2. Generate candidate mutants and the task skeleton:

   ```bash
   repoevals author mutants test_<area>_<function> path/to/module.py --function <function> --max 20
   ```

   For non-Python code, write 5–8 mutant patches by hand in `mutants/`.
3. Write reference tests in `oracle_tests/<path in the repository>` that check the function's observable behaviour.
4. Remove mutants the reference tests do not detect (they are usually equivalent to the original):

   ```bash
   repoevals author prune-mutants test_<area>_<function>
   ```

5. Keep at least five mutants. Write `instruction.md`: which code to test, where tests belong, how they are run, that
   library code must not change, and that the answer must end with `{"targets": ["<test file>"]}`.
6. Run `repoevals check <task_id>`.

## Phase 6 — Numerical tasks (numerical libraries)

1. Pick a routine with a trusted reference implementation (NumPy, SciPy, scikit-learn, mpmath, or a closed form).
2. Either use a historical accuracy fix (`repoevals author fix` gives you the base and `oracle.patch`), or inject a
   realistic accuracy bug with `setup.patch` (for example, remove a max-subtraction or a compensated sum).
3. Write `probe.py` with `CASES`, `candidate(case)` (import the library inside the function) and `reference(case)`.
   Cover very large and very small magnitudes, NaN and Inf, empty and non-contiguous inputs, and every supported
   dtype.
4. Set tolerances per dtype from the project's own test helpers, not from guesses.
5. Run `repoevals check <task_id>`.

## Phase 7 — Verify everything

```bash
repoevals validate --source .
repoevals check --repeats 3 --json .agent-evals/check.json
```

Fix or drop every task until validation shows 0 errors and every kept task is `OK`. The only acceptable warnings
are the draft-status warnings. If any task still has a symbolic base (`HEAD`, a branch), run `repoevals author pin`.

Re-read every `instruction.md` once more and remove anything that hints at the solution.

## Phase 8 — Report

Write `.agent-evals/AUTHORING_REPORT.md`:

```markdown
# repoevals authoring report — <repository>

Generated by <agent and model> on <date>. All tasks are drafts awaiting human review.

## Environment
- Python / toolchain: ...
- Build and test commands used: ...
- Hardware assumptions: ...

## Tasks
| task | family | source | what it tests | difficulty | check |
|---|---|---|---|---|---|
| fix_linalg_svd_order | fix | PR #1234 | SVD of Fortran-ordered input | medium | OK (3/3) |

## Dropped candidates
| candidate | reason |
|---|---|
| 3f2a91c | hidden tests pass before the fix |

## Needs human attention
- instructions that may still be too revealing or too vague: ...
- gold review threads that were judgement calls: ...
- tasks that depend on assumptions about the environment: ...

## Review checklist for the maintainer
- [ ] Each instruction describes a problem a user could report, without the solution.
- [ ] Hidden tests check behaviour stated in the instruction.
- [ ] Review manifests and gold threads are correct and complete enough.
- [ ] Task mix and difficulty match what the project wants to measure.
- [ ] Approve with `repoevals author approve <task_id> ...`.
```

Finish by printing the task table and the dropped-candidates table.
