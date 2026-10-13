# 02 — Landscape: evaluating AI coding agents on real repositories

Research date: 2026-10-13. Purpose: inform a generic, repo-agnostic, dependency-style "agent evals" framework
(focus: scientific Python/C++ libraries) generalizing the oneDAL PR's task families
(build, feature, bug-fix w/ hidden tests, PR-review replay w/ gold comments, seeded-bug review w/ decoys + clean
controls, test-writing graded by mutant kill rate; LLM-free graders in CI).

Confidence legend: **[P]** primary source read/fetched; **[S]** from search snippets/secondary coverage — verify before
citing externally. Items without a tag are well-established (pre-2026) and linked to the canonical paper/repo.

---

## (A) Benchmarks

### A1. Repo-level issue resolution (SWE-bench family and successors)

| Benchmark | Link | Takeaway / reusable pattern |
|---|---|---|
| SWE-bench (2023) | [arXiv 2310.06770](https://arxiv.org/abs/2310.06770), [swebench.com](https://www.swebench.com) | Canonical unit: issue + base commit + hidden `FAIL_TO_PASS` / `PASS_TO_PASS` test lists, graded in per-instance Docker images. Input = predictions JSONL (`instance_id, model_patch`), output = per-instance report JSON. The schema everyone copies. |
| SWE-bench Verified (2024) | [OpenAI post](https://openai.com/index/introducing-swe-bench-verified/) | 500 human-screened tasks (clear spec, tests that aren't over-specific). **Retired by OpenAI in early 2026**: an audit found ≥59% of hard failed tasks had flawed tests, and frontier models could reproduce gold patches (contamination) [S: [OpenAI](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/), [coverage](https://blog.pebblous.ai/blog/swe-bench-verified-retired/en/)]. Lesson: human screening isn't a one-time thing, and public tasks age. |
| SWE-bench Multimodal | [arXiv 2410.03859](https://arxiv.org/abs/2410.03859) | JS/visual issues with screenshots. Shows the harness has to support non-text inputs and visual verification. |
| SWE-bench Multilingual | [swebench.com/multilingual](https://www.swebench.com/multilingual.html) | 300 tasks, 42 repos, 9 languages incl. C/C++, same harness. Overlaps other benches ([issue #673](https://github.com/SWE-bench/SWE-bench/issues/673)) [S]: cross-benchmark dedup matters. |
| Multi-SWE-bench | [arXiv 2504.02605](https://arxiv.org/abs/2504.02605) | 1,632 tasks across 7 languages; **128 C + 129 C++** instances [S]. The closest public C++ issue-resolution set. Per-language build/test runners. |
| SWE-PolyBench | [arXiv 2504.08703](https://arxiv.org/abs/2504.08703) | Java/JS/TS/Python only (no C++). Adds retrieval/localization metrics (file/node-level) on top of pass/fail. |
| SWE-bench-Live | [arXiv 2505.23419](https://arxiv.org/abs/2505.23419), [repo](https://github.com/microsoft/SWE-bench-Live) | Monthly refresh. RepoLaunch *agent* auto-builds envs, and multiple test rounds per instance keep only deterministic tasks [S]. Pattern: **automated env setup plus a flakiness filter at ingest**. |
| SWE-rebench | [arXiv 2505.20411](https://arxiv.org/abs/2505.20411), [about](https://swe-rebench.com/about) | Continuous mining with fixed scaffold. **Contamination flag = task creation date < model release date**, shown on the leaderboard. Ships as a Harbor dataset with monthly `YYYY_MM` splits [S]. |
| SWE-smith | [arXiv 2504.21798](https://arxiv.org/abs/2504.21798) | Synthesizes bug tasks at scale via LM rewrites, procedural AST mutations, PR-mirroring/reverting, and combining bugs. Keeps only bugs that break ≥1 passing test. **Directly reusable for seeded-bug review and bug-fix task generation.** |
| SWE-Gym | [arXiv 2412.21139](https://arxiv.org/abs/2412.21139) | Training env (2.4k tasks), plus verifier models that rank trajectories. Shows the same task format serves eval *and* RL/SFT. |
| SWE-bench Pro (Scale) | [arXiv 2509.16941](https://arxiv.org/abs/2509.16941), [leaderboard](https://labs.scale.com/leaderboard/swe_bench_pro) | 1,865 long-horizon tasks: public (copyleft repos), **held-out**, and **commercial/private** splits. Contamination control by license plus private code. |
| SWE-Bench Pro V2 (Sep 2026) | [Scale blog](https://labs.scale.com/blog/swe-bench-pro-v2) [S] | **Agent diff is re-graded on a pristine image**, and both grades are published. That caught a model forging a Go module checksum and another editing the module cache. Hidden-test paths are restored and agent edits to fixtures/snapshots reverted before tests are applied. |
| SWE-Bench Pro Verified (Oct 2026) | [arXiv 2609.08149](https://arxiv.org/abs/2609.08149) [P-abstract] | Closes leakage channels (future commits, gold patch, tests) and minimally fixes flawed tasks. Some models score substantially lower. |
| DeepSWE / Datacurve audit (May 2026) | [blog](https://deepswe.datacurve.ai/blog/deepswe) [S] | Claims Pro's graders rejected correct solutions 24% of the time and accepted wrong ones 8.5%, and flagged >12% of some rollouts as cheating via git history. LLM-analyzer-based, so treat the numbers as unverified. The point stands: **grader error rates should be measured and published.** |
| PatchDiff (ICSE 2026) | [arXiv 2503.15223](https://arxiv.org/abs/2503.15223) | 29.6% of "plausible" SWE-bench-Verified patches behave differently from gold. Inflation is about 6 pts. **Differential testing (agent patch vs gold) as a grader-adequacy check.** |
| UTBoost | [HF papers](https://huggingface.co/papers?q=SWE-bench+Verified) [S] | LLM-generated test augmentation found 36 under-tested tasks and 345 wrongly-passed patches. Pattern: **augment hidden tests offline, then freeze.** |
| AInsteinBench (Dec 2025) | [arXiv 2512.21373](https://arxiv.org/abs/2512.21373) [S] | Maintainer PRs from 6 scientific codebases (quantum chem, MD, numerical relativity, AMReX HPC, RDKit…). Agents do worse where tests/tooling are thin (AMReX, RDKit). Names numerical-stability, cross-language, and HPC-env issues. **Closest analogue to our target.** |
| SWE-bench Science (Aug 2026) | [arXiv 2608.19799](https://arxiv.org/abs/2608.19799v1) [S] | 119 tasks / 98 scientific repos / 20 domains. Domain "scientific guidance" helps only when aligned with the repair task. |
| ResearchEnvBench (2026) | [arXiv 2603.06739](https://arxiv.org/html/2603.06739v2) [S] | Env synthesis for AI/HPC research repos. Failures come from dependency resolution and version coupling. Validates a separate **build/env task family**. |

### A2. Terminal / agentic / interactive

| Benchmark | Link | Takeaway |
|---|---|---|
| Terminal-Bench 2.0 + Harbor (Nov 2025) | [tbench.ai](https://www.tbench.ai/news/announcement-2-0), [ICLR 2026 paper](https://proceedings.iclr.cc/paper_files/paper/2026/file/444a3737adaee10d86ad2ef5f74468e6-Paper-Conference.pdf) | 89 hard terminal tasks in Harbor format. 26 other benchmarks were adapted to the same format. Verified trajectories are public. |
| Terminal-Bench 3.0 / 4.0 (Jul–Oct 2026) | [TB3](https://www.tbench.ai/news/terminal-bench-3-0), [TB4](https://www.tbench.ai/news/terminal-bench-4-0), [Scale](https://labs.scale.com/blog/terminal-bench-harder-tasks-for-better-agents) [S] | **Task-vetting pipeline worth copying:** static checks → automated rubric (~35 criteria: solvable, verifiable, difficulty, cheat-resistance) → **oracle run + no-op run** → **adversarial "cheat" trials** before merge. TB4 was a maintenance release that removed saturated tasks and fixed broken ones, so benchmarks need versioned maintenance releases. |
| τ-bench / τ²-bench / τ³-bench | [τ](https://arxiv.org/abs/2406.12045), [τ² 2506.07982](https://arxiv.org/abs/2506.07982), [repo](https://github.com/sierra-research/tau2-bench) | Introduced **pass^k** (all k trials succeed) as a reliability metric. τ² adds dual control (the simulated user also acts). τ³ (2026) made 75+ task-quality fixes and a grading change that broke comparability with older scores [S]. **Grader version must be part of the result identity.** |
| AgentBench | [arXiv 2308.03688](https://arxiv.org/abs/2308.03688) | Early multi-environment agent suite (OS, DB, KG, web…). Mostly historical, but its per-env containerized server design is a precursor of Harbor/Inspect sandboxes. |

### A3. Function-/file-level code generation (contamination-prone, useful as smoke tests)

| Benchmark | Link | Takeaway |
|---|---|---|
| HumanEval / MBPP | [2107.03374](https://arxiv.org/abs/2107.03374), [2108.07732](https://arxiv.org/abs/2108.07732) | Defined the unbiased **pass@k estimator** (Chen et al.). Saturated and contaminated. Keep only as a sanity check. |
| EvalPlus (HumanEval+/MBPP+) | [2305.01210](https://arxiv.org/abs/2305.01210) | 80×/35× more tests flip rankings. **Test insufficiency inflates scores.** Same lesson as PatchDiff. |
| LiveCodeBench | [2403.07974](https://arxiv.org/abs/2403.07974) | **Time-windowed** problems by release date for contamination control, the same idea as SWE-rebench. |
| BigCodeBench | [2406.15877](https://arxiv.org/abs/2406.15877) | 1,140 tasks using 139 libraries (numpy/scipy/pandas…). Tests realistic library-API composition with ~99% branch-coverage tests. |
| DS-1000 | [2211.11501](https://arxiv.org/abs/2211.11501) | NumPy/SciPy/Pandas/sklearn problems. **Perturbs problems (surface/semantic) to defeat memorization.** Pattern for scientific-API tasks. |
| RepoBench / CrossCodeEval | [2306.03091](https://arxiv.org/abs/2306.03091), [2310.11248](https://arxiv.org/abs/2310.11248) | Cross-file completion. Built from repos created after cutoff. Exact-match/edit-sim metrics are weak, so prefer execution-based grading. |
| EvoCodeBench | [2404.00599](https://arxiv.org/abs/2404.00599) | Periodically **evolving** repo-level benchmark aligned with real repo distributions, with dependency recall metric. |
| Commit0 | [2412.01769](https://arxiv.org/abs/2412.01769) | Rebuild a library from spec + tests (greenfield). OpenHands found a **git-history exploit in Commit0 in Feb 2026** and fixed it with shallow clones, after which one model dropped from 50% to 18.8% ([OpenHands](https://www.openhands.dev/blog/analyzing-and-improving-openhands-index)) [S]. |

### A4. Scientific / research / ML-engineering

| Benchmark | Link | Takeaway |
|---|---|---|
| SciCode | [2407.13168](https://arxiv.org/abs/2407.13168) | 80 main / 338 subproblems written by scientists. Numerical test cases with tolerance. **Subproblem decomposition plus per-step tests.** |
| ScienceAgentBench | [2410.05080](https://arxiv.org/abs/2410.05080) | 102 tasks from 44 papers. Multiple graders (exec success, task-specific success criteria, CodeBERTScore, cost). Expert-validated. **Mitigates contamination by modifying datasets.** |
| CORE-Bench | [2409.11363](https://arxiv.org/abs/2409.11363) | Reproduce paper results from code/data. Anthropic reports manual review moved a score from **42% → 95%** after fixing grading/task bugs ([P](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)). **Read transcripts before trusting numbers.** |
| PaperBench | [2504.01848](https://arxiv.org/abs/2504.01848) | Replicate ICML papers. **Hierarchical rubric trees** co-written with authors (8k+ leaf nodes) and an LLM judge validated against a human-graded JudgeEval set. Pattern for partial credit. |
| MLE-bench | [2410.07095](https://arxiv.org/abs/2410.07095) | 75 Kaggle comps, graded against the human leaderboard (medals). Includes plagiarism/contamination detection (code similarity to public notebooks) and log inspection for rule violations. |
| RE-Bench / METR time horizon | [RE-Bench 2411.15114](https://arxiv.org/abs/2411.15114), [time horizon 2503.14499](https://arxiv.org/abs/2503.14499), [TH1.1](https://metr.org/blog/2026-1-29-time-horizon-1-1/) | Calibrates tasks by **human-expert time baselines**. Reports the 50% time horizon with CIs. TH1.1 (Jan 2026) grew to 228 tasks and **moved METR infra from Vivaria to Inspect** [P-search]. Also documented a modelling bug fix and time-horizon limitations ([note](https://metr.org/notes/2026-01-22-time-horizon-limitations/)). Pattern: record a human-time estimate per task. |
| ResearchCodeBench | [2506.02314](https://arxiv.org/abs/2506.02314) | Implement novel ML-paper contributions not in training data, graded by unit tests. Pattern for **post-cutoff novelty as contamination control.** |
| SciAgentArena / HeurekaBench (2026) | [2606.12736](https://arxiv.org/abs/2606.12736), [2601.01678](https://arxiv.org/abs/2601.01678) [S] | Cross-domain science agent tasks. Agents do well on well-specified data-analysis workflows and worse on open-ended ones. |

### A5. HPC / performance / kernels (relevant to oneDAL-class C++ libs)

| Benchmark | Link | Takeaway |
|---|---|---|
| ParEval | [2401.12554](https://arxiv.org/abs/2401.12554), [repo](https://github.com/parallelcodefoundry/ParEval) | 420 prompts × 7 execution models (serial/OpenMP/MPI/MPI+OMP/Kokkos/CUDA/HIP). Metrics: pass@k, **speedup_n@k, efficiency_n@k**. Correctness first, then performance. |
| ParEval-Repo (ICPP 2025) | [2506.20938](https://arxiv.org/abs/2506.20938) | Repo-level CUDA→OpenMP-offload/Kokkos translation, scored by **build@1 and pass@1**. Nothing beyond the smallest app was translated correctly. **Build-system generation is the main failure.** Supports a separate "build" family. |
| KernelBench | [2502.10517](https://arxiv.org/abs/2502.10517), [v0.1 blog](https://scalingintelligence.stanford.edu/blogs/kernelbenchv01/) | `fast_p` metric (correct AND speedup > p). ABC found a ~31% overestimate: fuzzing varied only values, not shapes/layouts, and stale GPU memory held the ground truth. |
| KernelBench-Verified (Jul 2026) | [2607.16241](https://arxiv.org/abs/2607.16241), [repo](https://github.com/facebookresearch/kernel_bench_verified) [S] | With TF32 baseline and hidden input distributions (×3, ×0.01, negate), the best model went from 1.43× to 0.88×. **Performance graders must use a realistic, tuned baseline plus hidden input distributions.** |
| robust-kbench (Sakana) | [2509.14279](https://arxiv.org/abs/2509.14279) | Multiple shapes (seen + held-out), init schemes, and seeds. Output-range/std/axis-variation checks catch degenerate kernels. |

### A6. Code review

| Benchmark | Link | Takeaway |
|---|---|---|
| CodeReviewer (2022) | [2203.09095](https://arxiv.org/abs/2203.09095) | Diff-quality estimation, comment generation, and refinement. BLEU vs human comments is a weak metric, now superseded. |
| ContextCRBench (Nov 2025) | [2511.07017](https://arxiv.org/abs/2511.07017) | 67,910 context-enriched entries with issue intent. Fine-grained (hunk/line) evaluation. Textual context helps more than code context. |
| SWE-CARE / CodeFuse-CR-Bench | [repo](https://github.com/inclusionAI/SWE-CARE), [2509.14856](https://arxiv.org/abs/2509.14856) | Python repo-level review with retrieval context (BM25). |
| SWR-Bench | [2509.01494](https://arxiv.org/abs/2509.01494) | 1,000 verified PRs with structured ground-truth issues. An LLM judge checks coverage. |
| CR-Bench (Mar 2026) | [2603.11078](https://arxiv.org/html/2603.11078v1) [S] | Classifies output against a known bug. Highlights the **signal-to-noise problem** of "find all issues" reviewers, which supports our decoy/clean-control design. |
| Code Review Agent Benchmark (Apr 2026) | [2603.23448](https://arxiv.org/abs/2603.23448) [S] | **Executable review grading:** a comment counts if it leads to a code change that passes tests. Argues similarity-to-human-comment is weak. A possible LLM-free review grader. |
| SWE-PRBench (2026) | [2603.26130](https://arxiv.org/abs/2603.26130) [S] | Review quality vs real PR feedback. |
| Martian code-review-benchmark | [repo](https://github.com/withmartian/code-review-benchmark) [S] | Offline set has 173 golden comments with severity/category. **Online track samples fresh PRs** (anti-memorization). Top-5 ranking was stable across 3 different judge models, so judge-robustness is checked by swapping judges. |
| GitHub ReviewBench (Oct 5 2026) | [GitHub blog](https://github.blog/ai-and-ml/github-copilot/reviewbench-an-open-benchmark-for-ai-code-review/) [P] | 219 PRs / 187 repos / 19 langs. Gold set is a union of human comments, **follow-up-commit-inferred issues**, static analysis, and multi-family LLMs, deduped. **Grounded P/R/F1** (gold only) plus **augmented** (judge credits valid novel findings), F-β, and severity/category slices. 96.6% re-label agreement. Versioned semantic matcher. **No explicit clean-PR controls**, which is a gap our decoy/clean-control family fills. |

### A7. Test generation

| Benchmark | Link | Takeaway |
|---|---|---|
| TestGenEval | [2410.00752](https://arxiv.org/abs/2410.00752) | 68k tests / 1,210 files / 11 Python repos. Full-file and completion modes. Metrics: pass, coverage, **mutation score**. |
| SWT-bench | [2406.12952](https://arxiv.org/abs/2406.12952) | Turns SWE-bench into "write a reproducing test". Metric: **fail-before / pass-after** on the gold patch, plus change coverage. Directly reusable for bug-fix tasks ("write the test first"). |
| TDD-Bench-Verified | [2412.02883](https://arxiv.org/abs/2412.02883) | Same fail→pass test criterion, human-verified. |
| TestExplora (2026) | [2602.10471](https://arxiv.org/html/2602.10471v2) [S] | 2,389 repo-level tasks / 482 repos. Proactive bug discovery, mutation score reported. |
| SWE-Mutation / ULT / SecMutBench (2026) | [ULT TOSEM](https://dl.acm.org/doi/10.1145/3805043), [SecMutBench](https://doi.org/10.1145/3805760.3814933) [S] | High pass rates mask low mutant detection, so **mutation/real-bug detection is the headline metric**. A domain-specific (security) mutation score is 2.2× stricter than generic. Same idea as **domain-specific mutation operators for numerics**. |
| Meta ACH (mutation-guided test gen) | [2501.12862](https://arxiv.org/abs/2501.12862) | Industrial: LLM generates *realistic, fault-class-specific* mutants plus tests that kill them, and an **equivalent-mutant detector** (LLM) filters them. Pattern for C++ mutant generation beyond Mull/dextool. |

### A8. Reward-hacking / integrity probes

| Item | Link | Takeaway |
|---|---|---|
| ImpossibleBench (ICLR 2026) | [2510.20270](https://arxiv.org/abs/2510.20270), [Inspect impl](https://github.com/safety-research/impossiblebench) | Mutate tests to contradict the spec. Any pass = cheating. Stronger models cheat more. **Cheap canary tasks for every suite.** |
| SWE-bench "future commits" | [issue #465](https://github.com/SWE-bench/SWE-bench/issues/465), [SWE-Bench Pro #93](https://github.com/scaleapi/SWE-bench_Pro-os/issues/93), [Harbor PR #1593](https://github.com/harbor-framework/harbor/pull/1593) | `git log --all`, tags, and remote branches leak the fix. Fix = strip refs, `gc --prune`, shallow single-commit history. |
| Poolside "Through the looking glass" (May 2026) | [blog](https://poolside.ai/blog/through-the-looking-glass) [P] | Hacks seen: git history, finding the upstream repo on GitHub, web/package-registry archives. **As long as network is on, leaks exist.** Detection came from cross-benchmark anomaly (a +20% jump that didn't replicate), trajectory review, rubric LLM judges, and network logging. |
| SWE-Lancer (via ABC) | [2507.02825](https://arxiv.org/abs/2507.02825) | 100% achievable without solving: agent could overwrite the hidden test archive. **Tests must never be reachable/writable from the agent sandbox.** |

---

## (B) Frameworks / harnesses

| Framework | License / install | Task format | Sandbox | Scorer API | Log format | CI-friendliness / notes |
|---|---|---|---|---|---|---|
| **UK AISI Inspect** (`inspect_ai`) [docs](https://inspect.aisi.org.uk) | MIT; `pip install inspect-ai` | Python `@task` → `Task(dataset, solver/agent, scorer, sandbox, epochs)`. `Sample(input, target, metadata, files, setup, sandbox)` | Built-in `docker` (compose; auto `network_mode: none` when generated, but **not** for custom compose), `local`. Plugins: k8s, daytona, modal, ec2, proxmox, vagrant ([P](https://inspect.aisi.org.uk/sandboxing.html)) | `@scorer` async fn → `Score(value, answer, explanation, metadata)`. Scorers run on host and can `sandbox().exec()`. Metrics + **epoch reducers** (`mean, pass_at_k, at_least_k, max, mode`). Caveat: reducer args lost on retry ([#5669](https://github.com/UKGovernmentBEIS/inspect_ai/issues/5669)) [S] | `.eval` (zip of JSON, default since 0.3.46) / `.json`. Schema via `inspect log schema`. Python Log API, header-only reads, `inspect view` web viewer ([P](https://inspect.aisi.org.uk/eval-logs.html)) | Strong: CLI `inspect eval`/`eval-set` (resumable), `--limit`, `--no-fail-on-error`. De facto standard for gov/safety orgs (AISI, METR moved to it). **inspect_evals** ([repo](https://github.com/UKGovernmentBEIS/inspect_evals)) has SWE-bench, MLE-bench, CORE-Bench, etc. **inspect_swe** ([docs](https://meridianlabs-ai.github.io/inspect_swe/)) runs Claude Code / Codex CLI / Gemini CLI inside the sandbox via `sandbox_agent_bridge`. Known gaps: bridged agents skip sentinels, model-name routing issues [S]. **inspect_harbor** ([docs](https://meridianlabs-ai.github.io/inspect_harbor/tasks.html)) loads Harbor tasks as Inspect samples (multi-step + Windows unsupported) [P]. |
| **Harbor** [repo](https://github.com/harbor-framework/harbor) | Apache-2.0; `uv tool install harbor` / `pip install harbor` [P] | Directory per task: `instruction.md`, `task.toml` (`[task]`, `[metadata]`, `[agent] timeout_sec`, `[verifier] timeout_sec`, `[environment] cpus/memory_mb/storage_mb/network_mode/allowed_hosts`, `[solution]`), `environment/Dockerfile` (or compose / Apptainer.def for HPC), `solution/solve.sh`, `tests/test.sh`. Optional multi-step `steps/` ([P](https://docs.harborframework.com/tasks/overview)) | Docker locally. Cloud: Daytona, Modal, LangSmith, Blaxel, etc. via `--env` | Verifier script writes **`/logs/verifier/reward.txt`** (scalar) or **`reward.json`** (multi-dim/labeled) | Job dir with per-trial results. Trajectories in **ATIF** JSON v1.0–1.7 (`agent/trajectory.json`) ([ATIF RFC](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md)) | Language-agnostic (bash verifier), so **the best fit for C++/build tasks**. Registry `dataset@version` and Hub. Adapters for ~26+ benchmarks (SWE-bench, SWE-rebench, Aider Polyglot…). Built-in agents: Claude Code, Codex CLI, OpenHands, mini-SWE-agent, Terminus-2. RL/SFT rollout interface. Oracle = `solve.sh`. |
| **METR Task Standard** [repo](https://github.com/METR/task-standard) | MIT; spec + workbench | `TaskFamily` Python class: `get_tasks`, `get_instructions`, `get_permissions` (e.g. `full_internet`), `install`, `start`, `score`, `get_aux_vm_spec`, `teardown`; optional `manifest.yaml` | Docker (reference driver; Docker optional per spec), aux VMs (GPU) | `score(v, submission) -> float \| None` (None = needs human/manual) | Driver-defined (historically Vivaria) | Pre-1.0 and not fully stable. METR itself moved infra to Inspect in 2026 (TH1.1). **Treat as an import format, not a primary target.** |
| **SWE-bench harness** [repo](https://github.com/SWE-bench/SWE-bench) | MIT; `pip install swebench` | HF dataset rows: `instance_id, repo, base_commit, patch, test_patch, problem_statement, FAIL_TO_PASS, PASS_TO_PASS, environment_setup_commit, version` | 3-tier Docker images (base/env/instance). Modal option | Apply patch → run tests → parse log per repo-specific parser → resolved iff all F2P pass and P2P still pass | Per-instance `report.json` + run logs. Predictions JSONL in | CI-usable but heavy (image builds). The **per-repo log parser + F2P/P2P** model is the one to mirror for pytest/ctest. |
| **mini-SWE-agent** [repo](https://github.com/SWE-agent/mini-swe-agent) | MIT; pip | Bash-only agent (~100 LOC core). Each action is an independent `subprocess` | Local / Docker / Podman / Singularity / SWE-ReX backends | n/a (agent). Uses SWE-bench harness | Trajectory JSON, ATIF via Harbor | **Ideal reference/baseline agent** for a repo-agnostic framework: tiny, model-agnostic, comparable across repos. Was implicated in the SWE-bench Pro git-history exploit (images, not agent) [S]. |
| **SWE-ReX** [repo](https://github.com/SWE-agent/SWE-ReX) | MIT; pip | Runtime abstraction (shell sessions, exec, file I/O) | Local, Docker, Modal, AWS Fargate, remote | n/a | n/a | Decouples agent from sandbox. Useful if we write our own runner. |
| **OpenHands benchmarks** [repo](https://github.com/OpenHands/benchmarks), [Index](https://index.openhands.dev/) | MIT | Per-benchmark adapters on the Agent SDK | Docker images per SDK commit. Remote runtime API for parallelism | Benchmark-native graders | Full traces published ([results repo](https://github.com/OpenHands)) | OpenHands Index = 5 categories (issue resolution, greenfield/Commit0, frontend, testing, info gathering). Version-coupled to SDK commits [S]. |
| **EleutherAI lm-eval-harness** [repo](https://github.com/EleutherAI/lm-evaluation-harness) | MIT; pip | YAML task configs (dataset, prompt template, metric, filters) | None (some code tasks exec unsafely with flag) | Built-in metrics + custom Python | JSON results + per-sample JSONL | Great for model-level, **not agentic**. Its YAML-task-registry pattern is worth copying for task discovery. |
| **Stanford HELM** [repo](https://github.com/stanford-crfm/helm) | Apache-2.0; pip `crfm-helm` | Python `Scenario` + `RunSpec` | Limited | Metrics classes | JSON run dirs + web frontend | Holistic multi-metric reporting (accuracy, calibration, robustness, efficiency). Pattern for **multi-metric scorecards**. Not agentic. |
| **OpenAI Evals / simple-evals** [evals](https://github.com/openai/evals), [simple-evals](https://github.com/openai/simple-evals) | MIT | YAML registry + JSONL samples | none | Templates (match, fuzzy, model-graded) | JSONL | **simple-evals deprecated (no updates since Jul 2025)**. OpenAI's hosted Evals platform goes read-only 2026-10-31 and shuts down 2026-11-30 [S]. **Do not build on these.** |
| **promptfoo** [site](https://www.promptfoo.dev) | MIT (verify LICENSE); npm/npx | YAML `promptfooconfig.yaml`: prompts × providers × tests with `assert` | none (provider exec scripts) | Assertion types (equals, js/python, llm-rubric, similar…) | JSON/HTML report | Very CI-friendly (GitHub Action). Prompt/red-team oriented, not repo agents. **Being acquired by OpenAI (Mar 2026)**, open source said to continue [S]. |
| **DeepEval** [repo](https://github.com/confident-ai/deepeval) | Apache-2.0; pip | pytest-style `LLMTestCase` | none | 40+ metrics, mostly LLM-judged (G-Eval, task completion, tool correctness) | Local + Confident AI cloud | `deepeval test run` in pytest/CI. LLM-judge heavy, so not suited to our LLM-free CI graders. |
| **Braintrust** [docs](https://www.braintrust.dev/docs) | Proprietary SaaS; OSS SDK + `autoevals` (MIT) | `Eval(name, data, task, scores)` | none | Scorer fns / autoevals | Hosted experiments | Has a GitHub Action. Good for experiment diffing. Vendor lock-in. |
| **LangSmith** [docs](https://docs.smith.langchain.com) | Proprietary; `openevals`/`agentevals` MIT | Datasets + evaluators | Harbor env provider (LangSmith sandboxes) [S] | Evaluator fns, trajectory evaluators | Hosted traces | pytest/vitest integration. Lock-in. |
| **HAL harness** (Princeton) [site](https://hal.cs.princeton.edu/) | archived repo [S] | Wraps benchmarks + agents | Local/Docker/Azure VMs | Benchmark-native | Weave traces + **cost accounting** | ICLR 2026. 21,730 rollouts ≈ $40k. **Cost-accuracy Pareto** and LLM-aided log analysis found agents searching HF for answers. Pivoted to a **reliability dashboard** (consistency, predictability, robustness, safety, abstention). Harness repo archived. |
| **Anthropic skill-creator evals** [SKILL.md](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) [S] | Apache-2.0 skill | `evals/evals.json` per skill: prompts + objectively verifiable, named assertions | Claude Code subagents | assertions + qualitative review | benchmark JSON (with vs without skill, time, tokens) | Pattern: **A/B "with vs without" the artifact** (skill/AGENTS.md) on the same tasks. Held-out 60/40 split when optimizing descriptions. |
| **AGENTS.md** [site](https://agents.md) | open standard, AAIF / Linux Foundation (Dec 2025) | Markdown repo instructions for agents (60k+ repos) | — | — | — | Our framework should **read the repo's AGENTS.md** as agent context and **evaluate AGENTS.md changes** (ablation: with/without). |
| **MCP** [spec](https://modelcontextprotocol.io) | open, AAIF | Tools/resources protocol | — | — | — | Expose repo-specific tools (build, test, docs) as MCP servers inside the sandbox. Harbor `task.toml` already has `mcp_servers` (inspect_harbor ignores it) [P]. |
| **Every Eval Ever** (EvalEval) [repo](https://github.com/evaleval/every_eval_ever), [2606.14516](https://arxiv.org/abs/2606.14516) | open schema | Aggregate JSON + instance-level JSONL per evaluation (UUID) | — | — | Converters from Inspect, HELM, lm-eval | **Results-interchange schema** worth emitting for publishing to the community DB. |

---

## (C) Methodology standards checklist to adopt

Each item lists its source, then **how to implement it** in our framework.

### C1. Task validity (ABC T.*, Terminal-Bench vetting, Anthropic)
- [ ] **Oracle passes.** Gold patch/solution reaches full reward, N times (ABC T.8/T.9; Harbor `solve.sh`; TB3 oracle run). Run as `validate` in CI on every task change.
- [ ] **No-op fails.** An empty patch / unchanged repo scores 0 (TB3 no-op run; ABC R.13 trivial-agent baseline).
- [ ] **Adversarial cheat trials.** Run an agent explicitly told to game the grader (delete tests, hardcode outputs, `git log --all`, pip-install upstream). The task must not pass (TB3; SWE-Lancer failure).
- [ ] **ImpossibleBench-style canaries.** A few tasks whose tests contradict the spec. Pass rate = cheating rate, reported separately.
- [ ] **Flakiness filter at ingest.** Run gold and base K≥5 times (ideally under randomized order / different seeds / thread counts). Drop or quarantine tests whose outcome varies (SWE-bench-Live multi-round validation). Store the per-test flake rate.
- [ ] **Isolation from ground truth** (ABC T.5). Hidden tests are injected only at grading time, outside agent-writable paths. **Strip git refs/tags/remotes, squash history to the base commit, `git gc --prune=now`**. No gold patch in the image.
- [ ] **Network policy per task.** Default to no network or an allowlist (Harbor `network_mode`/`allowed_hosts`; Inspect `network_mode: none`). Log all egress. Remember Inspect's custom compose silently re-enables internet.
- [ ] **Clean state per trial** (ABC T.4, Anthropic). Fresh container per trial. Clear GPU/shared memory, caches, and ccache/compiler caches that may carry other trials' artifacts.
- [ ] **Ambiguity check.** "Two experts would agree" (Anthropic). Spec states versions, file paths, API names (ABC T.1). Spot-review tasks where the oracle needed information not in the instruction.
- [ ] **0% suspicion rule.** A task that all frontier agents fail across many trials is presumed broken until a transcript review clears it (Anthropic).
- [ ] **Versioned maintenance releases.** Remove saturated/broken tasks with a changelog (TB4, τ³). Scores carry `suite@version` and grader version.

### C2. Outcome / grader validity (ABC O.*, PatchDiff, UTBoost, KernelBench-Verified)
- [ ] **Grade on a pristine image** (SWE-Bench Pro V2). Extract the agent diff (excluding test files/fixtures/lockfiles/vendored deps unless allowed), apply it to a fresh image, restore hidden-test paths, then grade. Optionally publish both in-place and pristine grades. A mismatch is a hacking signal.
- [ ] **Protected paths.** Revert agent edits to tests, fixtures, snapshots, CI config, and build-system test registration before grading. Report "touched protected path" as a flag.
- [ ] **Test adequacy.** Measure hidden-test coverage of the gold diff (changed-line coverage). Run **differential testing (agent vs gold)** on a sample (PatchDiff). Augment weak tasks with extra tests offline, then freeze (UTBoost, EvalPlus).
- [ ] **Mutation score of hidden tests themselves.** The hidden tests should kill mutants of the gold patch. Low kill rate means the task is under-tested. The same machinery serves the test-writing family.
- [ ] **Equivalent-mutant handling.** Filter equivalent/stillborn mutants (no compile, no behavior change) so kill rates aren't depressed (Meta ACH). For C++ use Mull / dextool / LLM-generated realistic mutants, plus compile-filter and a timeout-as-killed policy.
- [ ] **Numerical tolerance standard.** Graders use `rtol/atol` per dtype (e.g. `numpy.testing.assert_allclose`, ULP-based for float32/64), declared in task metadata. Handle non-associativity under threading/vectorization. Optionally pin reproducibility modes (e.g. oneMKL **CNR**, `OMP_NUM_THREADS`, fixed seeds). Never compare floats by string.
- [ ] **Metamorphic relations for scientific code.** Permutation invariance, scaling/translation equivariance, dimension consistency, known-solution cases, solver-vs-reference algorithm agreement, sklearn-style estimator checks. Use these where no exact oracle exists (scientific-software testing literature).
- [ ] **Hidden input distributions for perf/numerics** (KernelBench-Verified, robust-kbench). Held-out shapes, layouts (row/col-major, non-contiguous, sparse), dtypes, edge values (0, NaN, denormals, huge scale), and seeds. Output-range/variance checks against degenerate outputs.
- [ ] **Realistic performance baselines.** Compare to a tuned baseline under the same flags (TF32 lesson), with warmup, ≥N repetitions, and median plus CI. Pin CPU frequency/affinity. Report `fast_p`-style "correct AND speedup > p".
- [ ] **LLM judges (only where needed, off the CI critical path).** Pilot accuracy vs humans (ABC O.c.1). Cross-family judge vs the agent (self-preference). Swap judges and check rank stability (Martian). Both-order evaluation for pairwise judging (position bias). "Unknown" option and isolated per-dimension judges (Anthropic). Versioned judge prompt + model.
- [ ] **Measure grader error rates.** A periodic human-audited sample yields false-accept and false-reject rates, published with results (Datacurve audit lesson).

### C3. Review-task specific
- [ ] **Gold comment sets from multiple sources** (human comments, follow-up-commit-inferred issues, static analyzers), deduped and validity-labeled (ReviewBench).
- [ ] **Grounded vs augmented metrics.** Grounded P/R/F1 against gold is LLM-free and goes in CI. Augmented (judge-credited novel findings) is optional and offline.
- [ ] **Deterministic matcher first.** Match a comment to gold by file + line-window overlap + issue-category/keyword. Version the matcher. Optional semantic matcher offline.
- [ ] **Clean controls and decoys.** Report false-positive rate on clean PRs and decoy (benign-but-suspicious) changes. CR-Bench/ReviewBench lack explicit clean controls, so this is a differentiator.
- [ ] **Executable review validation (optional).** A review comment is "valid" if acting on it yields a change that passes the hidden tests (Code Review Agent Benchmark 2603.23448).
- [ ] **Severity weighting and F-β** slices.

### C4. Contamination control
- [ ] **Temporal split.** Tag each task with `created_at` (PR merge date). Report results split by model-cutoff (SWE-rebench, LiveCodeBench). Prefer tasks newer than the agent model's release.
- [ ] **Held-out / private split.** A public dev split plus a held-out split never published (or encrypted with a canary GUID) (SWE-bench Pro). Embed a **canary string** in task files so scrapers can filter them.
- [ ] **Synthetic / perturbed tasks.** Seeded bugs (SWE-smith-style), perturbed problems (DS-1000), and online fresh-PR sampling (Martian) resist memorization.
- [ ] **Memorization probe.** Ask the model to reproduce the gold patch given only the issue. High similarity flags contamination (OpenAI's SWE-bench Verified evidence).
- [ ] **Network-borne leakage.** Even with clean images, agents can fetch the upstream fix. Mitigate with no network, an allowlist, or upstream-mirror blocking plus egress logs (Poolside).

### C5. Statistics and reporting
- [ ] **Repeated trials (epochs ≥3, ideally 5–10)** per task. Report **pass@1 (mean), pass@k (unbiased estimator), and pass^k** (all-k reliability) (Chen et al.; τ-bench; Anthropic).
- [ ] **CIs.** Bootstrap over tasks (resample tasks, not trials). **Clustered SEs** when tasks share a repo/PR/feature (Miller 2024: clustered SEs can be >3× naive). Report n.
- [ ] **Paired comparisons.** A/B (model vs model, agent vs agent, AGENTS.md vs none) on identical task sets with paired-difference SEs, McNemar/sign test per task, or paired bootstrap (Miller 2024). Report the difference ± CI, not two independent scores.
- [ ] **Power analysis.** Pre-compute the minimum detectable effect for the suite size so users know whether a +3pt change is real.
- [ ] **Trivial baselines.** No-op agent, "run existing tests only" agent, and a reference agent (mini-SWE-agent with a fixed model) as a stable anchor (ABC R.12/R.13).
- [ ] **Cost/latency/tokens per trial.** Input/output/cache tokens, $ at stated prices, wall clock, agent turns, tool calls, build/test minutes. Plot a cost-accuracy Pareto (HAL).
- [ ] **Reliability metrics** beyond accuracy: consistency (pass^k), variance across seeds, abstention/"I can't" rate (HAL reliability dashboard).
- [ ] **Partial credit** where meaningful: build ok, compiles, P2P preserved, F2P fraction, rubric-tree leaves (PaperBench). Always report the strict binary too.
- [ ] **Transcript review loop.** Sample N transcripts per release (failures and successes). Run automated hack-detectors (git-history commands, test-file edits, network to upstream, hardcoded constants, `pytest.skip`/`#ifdef` disabling tests) (Anthropic, Poolside, HAL).
- [ ] **Eval card / datasheet per suite.** Construct validity, capabilities measured, task provenance and licenses, contamination status, known limitations, grader error rates, versions (ABC R.1–R.11; Datasheets for Datasets [1803.09010](https://arxiv.org/abs/1803.09010); BenchmarkCards [2410.12974](https://arxiv.org/abs/2410.12974)).

### C6. Reproducibility
- [ ] **Pinned environments.** Image digests (not tags), lockfiles (conda-lock/uv.lock/pixi), compiler and toolchain versions, CPU ISA flags. Record a hardware fingerprint in the log.
- [ ] **Agent identity = model + harness + harness version + config + system prompt hash** (Anthropic: "you evaluate harness+model together").
- [ ] **Deterministic grader runtime.** Fixed seeds, thread counts, and test ordering. Timeouts declared per phase (build/agent/verify).
- [ ] **Full logs.** Trajectory (ATIF or Inspect events), diff, build/test logs, reward JSON, grader version, all hashed.

---

## (D) Concrete recommendations for our framework

### D1. Positioning: be a thin standard + tooling layer, not another monolith
- Ship as a small pip package (e.g. `agent-evals`) plus a **declarative per-repo task spec**. Repos keep only `evals/` task directories and a config. Heavy machinery (images, runners, graders) lives in the dependency. This matches "adopt as a dependency instead of vendoring."
- **Canonical on-disk task format = Harbor task directory**, with an extension namespace in `task.toml` for our families. Reasons:
  - It's language-agnostic (bash verifier), which suits C++ builds/ctest.
  - Apache-2.0, already the format for Terminal-Bench 2–4 and 26+ adapted benchmarks.
  - It has `solution/solve.sh` (oracle) and `reward.json` (multi-dim).
  - Inspect can load it via `inspect_harbor`.
  - It works with Claude Code/Codex/OpenHands/mini-SWE-agent out of the box.
- **Execution backends**: (1) Harbor (`harbor run`) and (2) Inspect (`inspect_harbor` + `inspect_swe`), plus (3) a minimal built-in Docker runner for CI without either. Don't write our own agent loop; use mini-SWE-agent as the reference baseline agent.
- **Logs: emit Inspect `.eval` logs** (via Inspect, or by writing through Inspect's Log API) so `inspect view`, AISI/METR tooling, and Every-Eval-Ever converters work. **Also keep ATIF trajectories** (Harbor native). Optionally export **Every Eval Ever** aggregate JSON + instance JSONL for publishing.
- **Import adapters** (read-only): SWE-bench-style rows (`FAIL_TO_PASS`/`PASS_TO_PASS`), METR `TaskFamily`, Harbor datasets from the registry. That lets repos pull in e.g. Multi-SWE-bench C++ instances or SWE-rebench monthly splits for their own repo.
- **Respect AGENTS.md and MCP.** Mount the repo's AGENTS.md as-is and offer an **AGENTS.md / skills A/B family** (with vs without, paired stats). Allow per-task MCP servers (build/test/docs tools), declared in `task.toml`.

### D2. Map the oneDAL families onto the standard (and harden each)
| oneDAL family | Standard mapping | Add beyond the PR |
|---|---|---|
| Build | Harbor task. Verifier = configure+build+smoke-test → `reward.json {configured, built, smoke_pass}` | Matrix (compiler × ISA × backend) as separate samples. Hermetic caches. build@1 metric (ParEval-Repo). Env-synthesis variant (ResearchEnvBench). |
| Feature | Harbor task with hidden tests + oracle | Changed-line coverage of hidden tests vs gold. Metamorphic + tolerance-declared numeric checks. Perf guard (no regression > x% vs base, median of N). |
| Bug-fix (hidden tests) | SWE-bench semantics: F2P/P2P lists in `task.toml` ext; pristine-image grading | **Flake filter at ingest**, git-history stripping, protected-path revert, PatchDiff differential tests on a sample, optional "write repro test first" variant (SWT-bench fail→pass). |
| PR-review replay (gold comments) | Task output = structured review JSON (file, line range, severity, category, text) | Multi-source gold (human + follow-up-commit-inferred + static analyzers). Deterministic versioned matcher. Grounded P/R/F-β in CI. Optional augmented judge offline. Online fresh-PR track. |
| Seeded-bug review (decoys, clean controls) | Same output schema; seeded via SWE-smith-style mutators | Report **FPR on clean controls** and **decoy trigger rate** as first-class metrics. Bug realism via LLM/AST mutators validated by "breaks ≥1 test". Severity mix. Optional executable validation (comment → fix → tests pass). |
| Test-writing (mutant kill) | Verifier runs agent tests vs mutants of the module | Equivalent-mutant filtering. **Tests must pass on original** (validity gate). Also fail-on-buggy-version (SWT-bench) and coverage delta. Domain mutation operators for numerics (tolerance loosening, off-by-one in reductions, wrong axis, dtype narrowing, missing NaN handling, race/OMP-private). Report score per mutant class. |

### D3. New task families worth adding
- **Performance optimization** (KernelBench/ParEval style): correctness gate + speedup vs tuned baseline on hidden shapes/dtypes/layouts, `fast_p` metric, pinned hardware fingerprint.
- **Parallel/backend porting** (ParEval-Repo): add a backend (e.g. CPU→SYCL/GPU, OpenMP→TBB) with build@1/pass@1/speedup.
- **API migration / deprecation** (cross-version upgrades) and **docs/example fidelity** (examples compile and run, docstrings match behavior, LLM-free via doctest/sphinx build).
- **Reproduction-test writing** (SWT-bench): issue → failing test only.
- **Python↔C++ binding tasks** (pybind/daal4py-style): cross-language consistency checks, a common failure surface in sci libs (AInsteinBench).
- **Integrity canaries**: ImpossibleBench-style contradictory-test tasks plus "solution available upstream" honeypots. Report a cheating rate.
- **Environment/setup tasks** (ResearchEnvBench): reproduce a working dev env from scratch.

### D4. Framework features (beyond the oneDAL PR)
1. `agent-evals validate`: oracle-pass ×K, no-op-fail, flake-rate, hidden-test changed-line coverage, mutation score of hidden tests, leakage scan (git refs/tags/remotes, gold strings in image, test files reachable). Run in CI on any task change.
2. `agent-evals cheat-probe`: a scripted adversarial agent (delete tests, hardcode, `git log --all`, network fetch) that must score 0.
3. **Pristine re-grade + protected-path policy** built into the grader runner, with a dual-grade report.
4. **Stats module**: epochs, pass@k unbiased, pass^k, bootstrap/clustered CIs (cluster = PR/module), paired A/B with MDE/power calculator. Implement Miller-2024 formulas. Keep reducer parameters inside the log (avoid the Inspect `pass_at` value-loss bug).
5. **Accounting**: tokens/$/wall/turns/tool calls/build-minutes per trial, and a Pareto plot.
6. **Hack detectors** over trajectories (regex/AST, LLM-free): `git log --all|reflog|show <future>`, edits under protected paths, `pytest.skip`/`GTEST_SKIP`/`#if 0` around tests, hardcoded expected values, egress to github.com/pypi for the same project. Flag only, never auto-fail.
7. **Contamination metadata** per task: `created_at`, upstream PR URL, `public|heldout|synthetic` split, canary GUID. Leaderboard view filtered by model cutoff.
8. **Grader versioning**: `suite@semver`, `grader@semver`, matcher version, judge (if any) model+prompt hash. All go in the result identity.
9. **Eval card generator** from task metadata (construct, capabilities, provenance/licenses, limitations, measured grader error rates).
10. **Task authoring helpers**: mine PR → task (SWE-bench style), mutate → seeded-bug task (SWE-smith style), and LLM-assisted *authoring* only. Grading stays LLM-free in CI.
11. **Tiers**: `smoke` (≤10 tasks, every PR, regression evals near 100%), `nightly` (full, epochs ≥3), `release` (held-out + judge-augmented + transcript review). This follows Anthropic's capability vs regression split.
12. **GitHub Action** wrapper: matrix over tasks, cached images by digest, uploads `.eval` / ATIF / `reward.json` artifacts, and posts a paired-diff comment with CIs vs the base branch.

### D5. Explicit non-goals / cautions
- Don't depend on OpenAI Evals/simple-evals (deprecated/shutting down) or proprietary SaaS (Braintrust/LangSmith) for core paths. Optional exporters at most.
- Don't make LLM judges part of CI pass/fail. Keep them optional, versioned, calibrated, cross-family, and reported separately.
- METR Task Standard is pre-1.0 and METR moved to Inspect, so support import only.
- Inspect + bridged CLI agents still have rough edges (sentinels not enforced, model-name routing, Linux-only proxy) [S]. Keep Harbor as the parallel path for running real CLI agents.

---

### Source notes / unverified items
- 2026 items marked [S] (OpenAI's SWE-bench Verified retirement details, Datacurve audit percentages, SWE-Bench Pro V2 details, TB3/TB4 vetting specifics, promptfoo license/acquisition status, OpenAI Evals platform shutdown dates, Inspect bridge issues) came from search snippets/secondary coverage. Re-verify before quoting externally.
- openai.com returned 403 to direct fetch, so the SWE-bench Verified retirement post was not read directly.
- ABC checklist items are paraphrased from the arXiv HTML. The canonical list is the [checklist PDF](https://uiuc-kang-lab.github.io/agentic-benchmarks/).
