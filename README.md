# agentexam

**An exam for AI coding agents, set in your own repository: deterministic tasks, hidden answers, graded without an LLM.**

`agentexam` is a small, repo-agnostic framework for answering questions like *"Does our `AGENTS.md` actually help?"*,
*"Which agent fixes our numerical bugs without breaking tolerances?"*, or *"Can this agent review our PRs without
drowning maintainers in false alarms?"* — measured on your own code, with tests and graders the agent never sees.

A repository adopts it with **one config file and a folder of tasks**. The framework, its graders, statistics, and
CI action live here, so each project does not have to vendor thousands of lines of eval code.

```
your-repo/
  agentexam.toml                  # ~20 lines: test adapter, test/library globs, guidance files
  .agent-evals/tasks/
    fix_kmeans_empty_cluster/   # task.toml + instruction.md + setup.patch + hidden/tests/...
    review_seeded_softmax/      # task.toml + review.patch + manifest.json (seeds, decoys)
    test_chebyshev/             # task.toml + mutants/*.patch + oracle_tests/
```

```bash
pip install "agentexam @ git+https://github.com/vchamarthi/agentexam"   # PyPI release pending
agentexam validate                                      # static checks, seconds
agentexam check                                         # null agent fails, reference passes, spam rejected — no LLM, no secrets
agentexam run --agent claude-code --arm guidance:raw --arm guidance:none --repeats 5
agentexam report runs/<stamp>                           # pass rates with task-clustered CIs, paired arm deltas, pass@k, cost
```

> Status: **alpha (0.1)**. The core, the graders, and the example suite are covered by end-to-end tests. Intel
> scientific-library task packs are scaffolds — see [Roadmap](#roadmap).

## Where this comes from

`agentexam` generalizes the design of [uxlfoundation/oneDAL#3818](https://github.com/uxlfoundation/oneDAL/pull/3818)
("agent-eval tasks and graders for oneDAL, with no-LLM CI"). That PR introduced, for one C++ library, the families and
rules reused here: hidden-test bug fixes mined from merged PRs, seeded-defect review with decoys and a clean-change
control, human-review replay, test writing graded by mutant kill rate, history-free workspaces, and a CI self-check
proving every task is solvable and every grader rejects the untouched tree. A reviewer on that PR noted that most of
the grader logic is generic and does not belong in the library repository; `agentexam` is that extraction, made
build-system-agnostic, hardened, and extended with results from the 2025–26 agent-evaluation literature.
See [docs/research/01-pr3818.md](docs/research/01-pr3818.md) for a file-by-file analysis.

## Task families and graders

| family | grader (`[grader].type`) | the agent must… | graded by |
|---|---|---|---|
| `fix`, `feature` | `hidden_tests` | fix a bug / add behaviour described like an issue | tests overlaid only at grading + existing suites (`strict_pass`); edited tests are reset first |
| `build` | `command` | build, install or configure something | commands exit 0, artifacts exist with the right magic, tree left clean |
| `test` | `mutation` | write tests for untested code | tests pass on the real code and kill ≥ `min_kill` of hand-written mutants |
| `review` | `review_seeded` | review a change with planted defects | one-to-one matching of findings to seeds; recall, **precision**, F1; decoys (project idioms that look wrong) count as false alarms |
| `review` (control) | `review_seeded` with no seeds | review a clean change | passes only with no blocker/major and ≤ N findings |
| `review` | `review_replay` | review a real merged PR | location match against the human review threads; recall, precision |
| `numeric` | `numeric` | make a numerical routine accurate | a hidden probe compares against a trusted reference (NumPy/SciPy/mpmath) with per-dtype rtol/atol, NaN/Inf positions, ULP distance |
| `perf` | `perf` | make something faster without breaking it | correctness gate, then interleaved base-vs-candidate timing; pass if the bootstrap CI lower bound of the speedup ≥ target |

Graders are plugins: a repository can add its own in `[repo].graders = ".agent-evals/graders"`, and packages can
register them under the `agentexam.graders` entry-point group.

## What every run gets, for free

- **History-free workspace.** `git archive` of the base commit, task files and config removed, one root commit,
  reflog expired; a leak scan fails preparation if any other ref, remote, stash or history is present.
  (Agents mining git history for the upstream fix inflated SWE-bench-family and Commit0 scores.)
- **Integrity checks (LLM-free).** Skip/xfail markers, `GTEST_SKIP`, `DISABLED_` tests, or loosened tolerances added
  to tests; deleted tests; edits to protected files (e.g. skip/deselect lists); `git log`/`fetch`/`reflog` history
  mining; network access; reads of grader material. Reported on every grade, enforceable with `[integrity].gate`.
- **Tamper-resistant grading.** Graders diff the tree against a copy of the history kept outside the workspace,
  with a private index (skip-worktree, `info/exclude`, deleted `.git` change nothing); hidden files are written
  through no agent-planted symlink; targets from answers are validated and shell-quoted; numeric candidates run in a
  separate process from the comparison; timeouts kill whole process groups (and containers).
- **Reproducible grading.** Grading snapshots the agent's tree and restores it afterwards; regrading gives the same
  answer. Infrastructure failures are `status: error` (not graded), never counted as agent failures.
- **Process metrics** from a vendor-neutral trace (`events.jsonl`, converters for Claude Code and Codex):
  commands, failures, guidance files actually read, tokens, cost.
- **Statistics that survive review.** pass@k (unbiased) and pass^k per task; macro pass rate with task-clustered
  bootstrap CIs; paired comparison between arms on the same tasks with an exact sign-flip test.
- **A/B arms for agent guidance.** `guidance:raw`, `guidance:none`, `guidance:file=<path>` — same tasks, same
  grader, only the instructions differ.
- **Harbor export (experimental).** `agentexam export harbor` writes Terminal-Bench/Harbor task directories
  (task.toml, instruction, oracle solution, a verifier that calls `agentexam grade`); the environment image must still
  be supplied per repository.
- **Guidance lint.** `agentexam guidance-lint` reports paths named in `AGENTS.md` & co. that no longer exist.

## The self-check is the product

`agentexam check` runs in CI on every PR that touches tasks, without a model or a secret, so it is safe on forks:

| probe | requirement |
|---|---|
| null agent (untouched workspace) | must **fail** — otherwise the task measures nothing |
| reference solution, `--repeats N` | must **pass** every time — otherwise the task is broken or flaky |
| reference tests vs mutants | must kill ≥ `oracle_min_kill` (default 100%) — otherwise the mutants are equivalent or invalid |
| findings spam (one finding per changed line) | must **fail** a review gate — otherwise recall-only scoring is gameable |

This follows the Agentic Benchmark Checklist (Zhu et al., 2025) and Terminal-Bench's task vetting: a task that the
do-nothing agent passes, or that its own reference fails, is a bug in the benchmark.

## Quick start (example repository)

```bash
git clone https://github.com/vchamarthi/agentexam && cd agentexam
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
pytest -q                                          # unit + end-to-end tests on examples/minisci

cp -R examples/minisci /tmp/minisci && cd /tmp/minisci && git init -q && git add -A && git commit -qm init
agentexam list
agentexam check                                      # 6 tasks, all OK
agentexam run --agent claude-code --repeats 3 fix_var_ddof review_seeded_softmax
agentexam report runs/<stamp>
```

Adopting in your repository: [docs/adopting.md](docs/adopting.md). Config reference: [docs/config.md](docs/config.md).
Task format: [docs/task-spec.md](docs/task-spec.md).

## Scientific Python focus (Intel ecosystem first)

The primary targets are numerical libraries where "the tests pass" is not the whole story: dtype matrices,
tolerances, SYCL devices, upstream test suites run against a drop-in replacement, and patch/fallback behaviour.
Starter configs live in [examples/intel/](examples/intel/) for **dpctl, dpnp, mkl_fft, mkl_random, mkl_umath,
scikit-learn-intelex (sklearnex, daal4py, onedal), xgboost**, and **oneDAL** (a mapping of PR #3818's tasks).
Highlights from the [ecosystem study](docs/research/04-scientific-adaptation.md):

- Everything except GPU-specific paths runs on free GitHub runners: a SYCL CPU device comes from Intel's OpenCL CPU
  runtime, as dpctl/dpnp/xgboost CI already do. GPU tasks declare `requires = { devices = ["gpu"] }` and are
  `skipped` (not failed) elsewhere.
- Skip/deselect lists (`deselected_tests.yaml`, `skipped_tests*.tbl`) are both a free source of hidden tests (a fix
  that removes entries comes with its own tests) and an attack surface, so configs mark them `protected_globs`.
- The numeric grader should reuse each repo's own tolerance helpers (e.g. dpnp's `assert_dtype_allclose`) and pin the
  reference library build, because NumPy builds can disagree (see dpnp#2914 review).

## Is this already done?

Pieces exist; the combination does not (survey of ~35 projects: [docs/research/03-prior-art-names.md](docs/research/03-prior-art-names.md)).
[Harbor](https://github.com/harbor-framework/harbor) has the closest task format (we export to it),
[SWE-bench](https://github.com/SWE-bench/SWE-bench)/[SWE-smith](https://github.com/SWE-bench/SWE-smith) the hidden-test
pattern as fixed datasets, [Inspect](https://inspect.aisi.org.uk/) the strongest general eval runtime, and several
2026 benchmarks cover review (ReviewBench, SWE-PRBench) or mutation-graded test writing (TestGenEval, SWT-bench,
SWE Atlas). None is a dependency that a repository adds to keep its *own* agent tasks next to its code, with review
precision/decoys/controls, mutation grading, guidance A/B arms, and an LLM-free CI self-check — and none targets
scientific libraries. The oneDAL PR author's runner (`napetrov/agent-benchmark`, unpublished at the time of
writing) is the closest relative; collaboration is preferable to divergence.

## Standards followed

Summarized in [docs/standards.md](docs/standards.md), with sources in
[docs/research/02-landscape.md](docs/research/02-landscape.md): Agentic Benchmark Checklist (task validity, outcome
validity, reporting), Anthropic's *Demystifying evals for AI agents* (start from real failures, separate capability
and regression suites, read transcripts), Miller 2024 error bars (clustered CIs, paired comparisons), tau-bench
pass^k, contamination controls from SWE-rebench/SWE-bench-Live (task `created` dates, filter by model cutoff), and
2026 findings on reward hacking (git-history mining, test tampering, ImpossibleBench-style canaries).

## Roadmap

- **0.2** — task packs for mkl_umath, mkl_random, sklearnex (mined fixes listed in
  [docs/research/04-scientific-adaptation.md](docs/research/04-scientific-adaptation.md)); `pytest` adapter: per-node
  regression baseline and "skip count must not grow"; Docker isolation for graders; Inspect `.eval` log export.
- **0.3** — `differential` grader (dpnp vs NumPy, sklearnex vs stock scikit-learn), `array_api_delta`, patching/
  fallback-log grader for sklearnex, SYCL device probe recorded in every grade, python-only overlay builds.
- **0.4** — executable review probes (reviewer reproducers as tests), ImpossibleBench-style canary tasks,
  fresh-image regrading, Every-Eval-Ever export, eval cards.
- Later — GPU lane on self-hosted Intel runners, free-threading (cp314t) task family, SWE-smith-style task synthesis.

## License and credits

Apache-2.0. Design credit to the oneDAL agent-evals work in uxlfoundation/oneDAL#3818 (see [NOTICE](NOTICE)).
