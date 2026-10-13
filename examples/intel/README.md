# Starter configs for Intel scientific libraries

Copy `<repo>.agentexam.toml` to the target repository root as `agentexam.toml`, then add tasks under
`.agent-evals/tasks/`. These configs encode what the [ecosystem study](../../docs/research/04-scientific-adaptation.md)
found about each repository's build, tests and agent guidance. **They are scaffolds: none has been run against
the real repository yet.**

| repo | adapter | CPU-only on GitHub-hosted runners | existing agent guidance | first tasks to mine |
|---|---|---|---|---|
| dpnp | pytest (`-m "not slow"`) | yes, via the OpenCL CPU runtime | none | #3081 (Python-only fix), #3043, #3063; review replay #3045, #2914 |
| dpctl | pytest + gtest | yes, via the OpenCL CPU runtime | none | seeded review from #2294 (`__dealloc` leak) |
| mkl_fft | pytest | yes | AGENTS.md + copilot instructions | numeric tasks from vendored NumPy/SciPy FFT tests |
| mkl_random | pytest | yes | none | #172 (`randint` could return `high`) fix + review replay |
| mkl_umath | pytest | yes | AGENTS.md + copilot instructions | #269 (`abs` of NaN), seeded review from #263 |
| scikit-learn-intelex | pytest | yes (`NO_DPC=1 NO_DIST=1`) | 8 AGENTS.md + 7 instruction files | #3357 (NaN fallback), #3348 (hidden tests = deselected sklearn tests); replay #3182 |
| xgboost | ctest / pytest | yes (CPU, SYCL-on-CPU) | none | mutation task from #12041 (SYCL fix shipped without a test); replay #11519, #11808 |
| oneDAL | bazel | yes (CPU tasks) | AGENTS.md + instructions | the 27 tasks of PR #3818 |

The PR numbers are as reported by the study. Check each one before turning it into a task.
