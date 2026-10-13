# 03 — Prior art and naming for a repo-agnostic agent-eval framework

> **Outcome (2026-10-13):** the project first shipped as `heldout` (rank 1 below), was briefly `agentexam`, and is
> now **`repoevals`**: the most literal name (evals that live in your repo), free on PyPI and npm, with no
> exact-name repositories on GitHub. Nearest name: the RepoEval benchmark (RepoCoder, 2023), a different thing.
> The ranking below is kept as researched.

Date: 2026-10-13. Scope: (1) is the generalized oneDAL PR #3818 idea already done? (2) names for the new project under GitHub `vchamarthi`.

Reference: [uxlfoundation/oneDAL#3818](https://github.com/uxlfoundation/oneDAL/pull/3818) (open, created 2026-09-28): 27 tasks in `dev/agent_evals/` (3 build, 13 fix/feature incl. 9 mined from merged PRs with hidden tests, 2 test-writing graded by 7 seeded mutants each, 9 review: planted defects + decoys + clean-PR control + 6 human-review replays). `run.py validate|static|check`; CI uses no model, no key. The agent runner ("agent-benchmark", reads `repo-eval.yaml`) is Intel-internal, "to be published".

---

## 1. Prior art

Stars/activity from `gh api` on 2026-10-13.

### A. General agent-eval harnesses and task formats (closest in shape)

| Project | Stars / activity | What it does | Overlap | Gap left |
|---|---|---|---|---|
| [harbor-framework/harbor](https://github.com/harbor-framework/harbor) (Laude Inst., Terminal-Bench authors) | 6.1k, pushed today | Generic task format: `instruction.md`, `task.toml`, `environment/` (Dockerfile/compose/Apptainer), `solution/solve.sh` (oracle), `tests/test.sh` that writes `reward.txt`/`reward.json`; multi-step tasks; `harbor run -a <agent>`; `harbor tasks check` + oracle runs in CI ([docs](https://docs.harborframework.com/tasks/overview)) | High: declarative per-task dirs, deterministic verifier, oracle solution, agent adapters (claude-code, codex, gemini-cli, goose…) | Built for benchmark datasets/RL rollouts, not tasks living inside the target repo. No repo-level config (base commit, history stripping, `strict_pass` on existing tests), no review or mutation families, no guidance A/B arms, no guidance claim checker, no reusable GitHub Action for downstream repos |
| [harbor-framework/terminal-bench](https://github.com/harbor-framework/terminal-bench) | 884 | Benchmark built on Harbor | Task-quality process (oracle must pass, CI validation) | Fixed public benchmark, not per-repo |
| [benchflow-ai/benchflow](https://github.com/benchflow-ai/benchflow) / [skillsbench](https://github.com/benchflow-ai/skillsbench) | 355 / 1.8k | Env framework; SkillsBench measures skills on vs off with deterministic verifiers + oracle ([paper](https://arxiv.org/abs/2602.12670)) | With/without-context arms; deterministic verifiers | Cross-domain benchmark; not repo-local; no review/mutation families |
| [UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai) + [inspect_evals](https://github.com/UKGovernmentBEIS/inspect_evals) + [inspect_swe](https://meridianlabs-ai.github.io/inspect_swe/) | 3.0k / 702 | Eval framework; inspect_swe runs Claude Code/Codex/Gemini CLI as agents in sandboxes | Possible backend for running agents | No repo task schema, no task families; you write Python evals |
| [promptfoo](https://github.com/promptfoo/promptfoo) ([coding-agent guide](https://www.promptfoo.dev/docs/guides/evaluate-coding-agents/)) | 26k | YAML evals; Claude Agent SDK / Codex SDK providers; per-row workspace copies; GitHub Action | YAML config, CI, coding-agent providers | Generic assertions; no hidden-test injection, oracle self-check, mutation or review grading; LLM-judge is the easy default |
| [OpenHands/benchmarks](https://github.com/OpenHands/benchmarks) | 128 | Eval harness for OpenHands V1 | Runs SWE-style benchmarks | Tied to OpenHands and public datasets |
| [openai/evals](https://github.com/openai/evals), [braintrustdata/autoevals](https://github.com/braintrustdata/autoevals), [langchain-ai/agentevals](https://github.com/langchain-ai/agentevals), [langfuse](https://github.com/langfuse/langfuse) | 19.6k / 1.1k / 748 / 35.8k | LLM/agent-app evals, trajectory evaluators, observability | Eval vocabulary | Not about coding agents changing a repo |
| [microsoft/ai-agent-evals](https://github.com/microsoft/ai-agent-evals) | — | GitHub Action for Foundry agents (LLM judge) | "Eval as a GitHub Action" | Foundry-only, not coding tasks |
| [github/gh-aw](https://github.com/github/gh-aw) (Agentic Workflows) | 5.4k | Runs Copilot/Claude/Codex as Actions workflows | Could be the agent runner | Runs agents; does not evaluate them |

### B. Task generation from arbitrary repos (SWE-bench family)

| Project | Stars | What it does | Overlap | Gap |
|---|---|---|---|---|
| [SWE-bench/SWE-bench](https://github.com/SWE-bench/SWE-bench) | 6.0k | Issue-resolution benchmark + Docker harness; FAIL_TO_PASS / PASS_TO_PASS | Exactly the "mined PR, hidden test, existing tests still pass" pattern (= oneDAL `strict_pass`) | Fixed dataset of 12 repos; adding your repo means building a dataset, not a config |
| [SWE-bench/SWE-smith](https://github.com/SWE-bench/SWE-smith) | 801 | Synthesizes bug tasks for any Python repo (LM rewrites, procedural mutations, PR inversion) | Seeded-bug generation for any repo; useful for seeding review/mutation tasks | Training-data focus; bug-fix only |
| [DeepSoftwareAnalytics/swe-factory](https://github.com/DeepSoftwareAnalytics/swe-factory) | 199 | LLM multi-agent builds env + test cmds for any GitHub repo | "Build your own SWE-bench" | Issue resolution only; LLM-built envs |
| [R2E-Gym](https://github.com/R2E-Gym/R2E-Gym), [SWE-Gym](https://github.com/SWE-Gym/SWE-Gym), [SWE-bench-Live](https://github.com/microsoft/SWE-bench-Live) | 340 / 748 / 254 | Procedural/commit-based task generation, live refresh | Mining tasks from commits | Training/benchmark datasets, not repo-maintained suites |
| [Sourcegraph CodeScaleBench](https://sourcegraph.com/blog/codescalebench-testing-coding-agents-on-large-codebases-and-multi-repo-software-engineering-tasks), Cognition FrontierCode, [ProdCodeBench](https://arxiv.org/html/2604.01527v1), [SWE Atlas (Scale)](https://arxiv.org/abs/2605.08366) | — | Vendor/research benchmarks; SWE Atlas includes Test Writing with a mutation pass + rubric | Families beyond bug-fix (Q&A, test writing, refactor) | Closed or fixed datasets; not a per-repo framework |

### C. "Does AGENTS.md / skills help?" (the motivating use case of #3818)

| Project | Stars | What it does | Gap |
|---|---|---|---|
| [Evaluating AGENTS.md (arXiv 2602.11988)](https://arxiv.org/abs/2602.11988), harness [eth-sri/agentbench](https://github.com/eth-sri/agentbench) | 63 | AGENTbench: NONE/LLM/HUMAN context arms on Python issue tasks; finds context files give no general lift, +20% cost | Research harness on fixed dataset; issue resolution only |
| [Vercel: AGENTS.md outperforms skills](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals) | — | Next.js API evals, AGENTS.md index vs skills | Single-framework internal suite |
| [Tessl repo evals](https://tessl.io/blog/three-context-eval-methodologies/) (beta, commercial) + [tessl-eval-demo](https://github.com/tesslio/tessl-eval-demo) | 0 | Scenarios from real commits, with/without "tile", rubric scoring | Closest commercial analogue; rubric/LLM-judged, not open, no no-LLM CI, no review/mutation families |
| [AndreaGriffiths11/agents-md-benchmark-plugin](https://github.com/AndreaGriffiths11/agents-md-benchmark-plugin) | 1 | Agent plugin + Node runner: baseline vs candidate AGENTS.md, diff/scope metrics | Behavioral heuristics, no correctness graders |
| [vltansky/agents-md-evals](https://github.com/vltansky/agents-md-evals) | 4 | A/B AGENTS.md rules via Anthropic skill-creator evals, prompts from git history | LLM-graded assertions |
| [igoregoshin/benchwright](https://github.com/igoregoshin/benchwright) (npm `benchwright`) | 6 | With/without benchmarking of skills/rules/MCP via headless agent CLIs | Skill-level, JS; no repo task families |
| [zhaiqiming/agents-md-eval-bench](https://github.com/zhaiqiming/agents-md-eval-bench), [alepot55/agentrial](https://github.com/alepot55/agentrial), [qualixar/agentassay](https://github.com/qualixar/agentassay) | 1 / 20 / 6 | Scoring layers / statistical agent testing | Small, generic |
| Readiness scanners: [napetrov/agentready](https://github.com/napetrov/agentready), [alipajand/agent-readiness-action](https://github.com/alipajand/agent-readiness-action) | 1 / 1 | Static, no-LLM scoring of AGENTS.md / repo readiness | Overlaps only with #3818's `run.py static` claim check; no agent runs |
| [napetrov/agent-benchmark](https://github.com/napetrov/agent-benchmark) | 0 | Intel doc-quality / skill benchmark ("measurement engine"); same author as #3818 | **Likely the "Intel-internal runner, to be published" referenced by #3818. Direct overlap risk — coordinate before building.** |

### D. Review and test-writing grading (the novel task families)

| Work | Relevance |
|---|---|
| [SWE-PRBench](https://arxiv.org/abs/2603.26130) (350 PRs, human review comments as gold, LLM-judge matching; 15–31% recall) | Same idea as #3818 review replay; dataset, not framework; LLM matcher (oneDAL uses file+line±5, no LLM) |
| [CRBugBench (arXiv 2610.13668)](https://arxiv.org/abs/2610.13668), [c-CRAB](https://arxiv.org/abs/2603.23448), [CR-Bench](https://arxiv.org/html/2603.11078v1) (signal-to-noise), [LangChain ReviewBench](https://www.langchain.com/blog/evaluating-code-review-agents-with-reviewbench) | Review-agent benchmarks; none expose decoys + clean controls as a reusable per-repo task type |
| [TestGenEval](https://testgeneval.github.io/) (mutation score), [SWE-Mutation](https://arxiv.org/abs/2605.22175), [SWT-bench](https://github.com/logic-star-ai/swt-bench), SWE Atlas test-writing | Mutation/fail-to-pass grading of agent tests; fixed datasets |

### Verdict

- **Not already done as a whole.** Each piece exists separately:
  - task format + oracle + deterministic verifier: Harbor
  - mined PR + hidden test + existing tests must pass: SWE-bench
  - with/without guidance arms: AGENTbench, Tessl, SkillsBench, and small A/B tools
  - review replay: SWE-PRBench
  - mutation-graded test writing: TestGenEval / SWE Atlas
- **No open project** lets any repo keep a small config + task folder next to its code, with:
  - all six task families: build / feature / hidden-test fix / review replay / seeded review with decoys and clean controls / mutation-graded test writing
  - an oracle self-check in CI with no LLM and no secrets ("untouched tree fails, reference passes")
  - a guidance-claim static check
  - a pip package + reusable GitHub Action
- **No harness targets scientific or numerical libraries.** SciCode and DS-1000 are snippet-level, and SWE-bench has only scikit-learn. Among the target repos, `mkl_fft`, `mkl_umath`, `scikit-learn-intelex` and `oneDAL` already ship `AGENTS.md` (checked via `gh api`), so the "does guidance help?" question is live for them.
- **Differentiators to lead with:**
  1. Tasks live in the target repo and maintainers review them there; the framework is shared.
  2. Review families measure false alarms (decoys, clean controls), not just recall.
  3. Test writing is graded by mutant kill rate, not coverage.
  4. CI has no LLM and no secrets (validate / static / oracle self-check), so it is safe on fork PRs.
  5. Guidance A/B arms are first-class.
  6. Scientific-library realities: CPU-only builds without vendor compilers, numerical tolerances, hardware-dependent tasks reporting `SKIPPED` instead of a false pass, and optional SYCL/oneAPI layers.
- **Risks:**
  - *Overlap with napetrov/agent-benchmark.* Talk to the #3818 author first: adopt, extend or co-own rather than fork the ecosystem.
  - *Duplicating Harbor.* Make Harbor the execution format: export or import Harbor task dirs, and use Harbor/inspect_swe as optional agent runners. Own only the repo manifest, task families, graders and CI Action.

---

## 2. Naming

Checks run 2026-10-13:
- GitHub: `gh api repos/vchamarthi/<name>` (404 = free).
- PyPI: `pypi.org/pypi/<name>/json` and `/simple/<name>/`, including `-` and `_` variants for the finalists.
- npm: `registry.npmjs.org/<name>`.
- crates.io: top 5 only.
- GitHub user/org with the same login.
- `gh search repos --match name` and web search for collisions.

PyPI 404 means not registered. PyPI can still reject a name as "too similar" to an existing one, so the name is only confirmed at first upload.

### 12 candidates

| # | Name | vchamarthi repo | PyPI | npm | Collisions found | Notes |
|---|---|---|---|---|---|---|
| 1 | `heldout` | free | free (also `held-out`, `held_out`) | free | Generic ML term; GH user `heldout` exists (0 repos); google-research/heldout-influence-estimation (unrelated) | Hidden tests, held-out gold review comments, held-out mutants. CLI `heldout run`, config `heldout.yaml` |
| 2 | `blindtrial` | free | free (also `blind-trial`) | free | None (GH, web, crates) | Blinded A/B arms (with/without AGENTS.md); the agent never sees graders |
| 3 | `benchtrial` | free | free (also `bench-trial`) | free | None | Bench = benchmark + lab bench; trial = controlled run |
| 4 | `repoproctor` | free | free (also `repo-proctor`) | free | None (`proctor` alone is taken on PyPI and npm; ProctorU is a different mark) | "Proctors an exam inside your repo" |
| 5 | `repo-eval` / `repoeval` | free | free | free | **RepoEval** is the name of the RepoCoder (2023) repo-completion benchmark; generic | Continuity with oneDAL's existing `repo-eval.yaml` |
| 6 | `trialkit` | free | free (also `trial-kit`) | free | GH org `trialkit` exists (0 repos) | Plain, toolkit-ish |
| 7 | `repoexam` | free | free (also `repo-exam`) | free | None | Clear, a bit flat |
| 8 | `evalbed` | free | free (also `eval-bed`) | free | None | "Testbed for evals"; low memorability |
| 9 | `agentlitmus` | free | free | free | **Litmus / LitmusChaos** (CNCF, 5.7k★) is a known DevOps brand | Medium trademark-confusion risk |
| 10 | `repo-assay` | free | free (`repoassay` free) | free | **GH org `RepoAssay`** ships repo-test-architect (test strategy tooling, MCP) in an adjacent space; `agentassay` and `assaykit` are taken in the AI-agent space | Good science nod, but crowded |
| 11 | `controlgroup` | free | free | free | Linux **cgroups** ("control groups") is confusing for a container-running tool; GH org exists | Rigor nod, ambiguity cost |
| 12 | `nullarm` | free | free | free | GH user `nullarm` exists (1 repo) | Null-hypothesis / control arm; obscure |

Eliminated during screening, with the reason:
- **Taken on PyPI:** `assay`, `assayer`, `agentassay`, `caliper`, `crucible`, `gauntlet`, `placebo` (boto record/replay lib), `litmus`, `rigor`, `rigour`, `yardstick`, `metron`, `holdout`, `benchtop`, `ablate`, `ablator`, `proctor`, `testtube`, `labrat`, `goldset`, `evalbench`, `evalforge`, `titrate`, `burette`, `agentbench` (THUDM, 3.8k★), `repobench` (a known benchmark).
- **Free on PyPI but taken on npm by a direct competitor:** `benchwright` (with/without skill benchmarking).
- **`agent-assay`:** blocked by the PyPI similarity check against `agentassay`.
- **Avoid entirely:** `petri` (Anthropic's auditing tool) and `inspect` (UK AISI).

### Top 5 (ranked)

1. **`heldout`**
   - Free on GitHub, PyPI (all spellings), npm and crates.io.
   - The name is the method: every grader relies on something held out from the agent (hidden tests, gold review threads, seeded mutants, decoys).
   - Short, lowercase, one word: `pip install heldout`, `heldout check`, `uses: vchamarthi/heldout@v1`.
   - Domain-neutral, so it doesn't read as Intel-only.
   - Downsides: a generic ML term makes a weak trademark and harder search ranking (pair it with a tagline like "heldout — agent evals that live in your repo"). The GH user `heldout` is squatted, which only matters if you later want a GitHub org of that name.
2. **`blindtrial`**
   - No collisions anywhere.
   - Strongest "rigor" signal: blinded, controlled A/B of guidance; the agent never sees graders. Distinctive for SEO.
   - Downsides: clinical connotation; 10 characters.
3. **`benchtrial`**
   - No collisions. "Bench" ties benchmark to lab bench (the science nod); "trial" says controlled run.
   - Reads well as a CLI (`benchtrial run`).
   - Downside: less self-explanatory than #1 or #2.
4. **`repoproctor`**
   - No collisions. Most descriptive: it proctors an exam for agents inside your repo, and the "repo-agnostic, repo-local" story is in the name.
   - Downsides: longer, and "proctor" carries slight surveillance baggage.
5. **`repo-eval`**
   - Free everywhere and matches the manifest name #3818 already uses (`repo-eval.yaml`), so the existing oneDAL work migrates with no renaming.
   - Downsides: generic; collides in name with the RepoCoder "RepoEval" benchmark. Better as the manifest filename than as the brand.

Recommendation:
- Brand: **`heldout`** (or `blindtrial` for a more distinctive mark).
- Manifest: keep the per-repo file as `repo-eval.yaml`, or `.heldout.yaml` with `repo-eval.yaml` accepted as an alias.

Not checked: domains, USPTO/EUIPO trademark databases, GitHub Marketplace Action names. Do a USPTO TESS search for the chosen name before the first release.
