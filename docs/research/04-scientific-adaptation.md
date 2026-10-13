# 04 — Adapting the oneDAL agent-eval framework to Intel's scientific Python ecosystem

Date: 2026-10-13. Research method: shallow clones in `(local shallow clones)/` (HEAD of the
default branch on 2026-10-13), read-only `gh api` / `gh pr` queries, PyPI JSON, a few web searches. Nothing remote was
created or modified. Claims marked **(unverified)** were not run; everything else was read from the repos or from GitHub.

---

## 0. TL;DR

- The oneDAL framework (`dev/agent_evals` in PR #3818: `repo-eval.yaml` manifest + `tasks/<id>/task.json`, families
  build / fix / feature / review / test, graders `test_pass`, `review`, `review_replay`, `mutation`, `bazel_cmd`,
  self-check `run.py check`, offline container, no LLM judge) carries over almost unchanged in *shape*. What changes:
  the **build/test/env adapters** (pytest + conda/pip + scikit-build/meson-python/setuptools + CMake instead of
  bazel/make), a **device-matrix layer** (SYCL CPU vs GPU), and **numerics-aware graders**.
- All 7 live target repos can run CPU-only on free GitHub-hosted runners: the SYCL CPU device comes from the
  **Intel OpenCL CPU runtime** (`intel-opencl-rt`, on the Intel conda channel, conda-forge and PyPI). Level Zero has
  no CPU device; anything that needs an Intel GPU, a no-fp64 device, multi-GPU, or GPU-only kernels needs
  self-hosted Intel GPU runners.
- `numba-dpex` is **archived** (2025-09-26) — exclude. `IntelPython/daal4py` now **redirects to
  `uxlfoundation/scikit-learn-intelex`** (daal4py lives in that repo) — treat as one repo with three test roots.
- Three repos already ship agent guidance, so the `guidance:none` vs `guidance:raw` arm comparison works out of
  the box: **mkl_fft** and **mkl_umath** (hierarchical `AGENTS.md` + `.github/copilot-instructions.md`), and
  **scikit-learn-intelex** (`AGENTS.md` in 8 dirs + 7 `.github/instructions/*.instructions.md`). dpctl, dpnp,
  mkl_random and xgboost have none.
- New Python-specific risks to design for: tests ship *inside* the package (`pytest --pyargs dpnp.tests`), so
  hidden tests must be injected into the installed tree. A fixed version may already be on PyPI or conda
  (network-off plus pinned envs are mandatory). The NumPy reference oracle is sometimes wrong itself (dpnp#2914:
  conda-forge NumPy and wheel NumPy disagree on `acosh(0+NaNj)`). Hypothesis-based suites (array-api-tests,
  xgboost) are nondeterministic unless seeded.

---

## 1. What carries over from oneDAL PR #3818 (baseline)

Read from `oneDAL@pr-3818/dev/agent_evals/` (branch HEAD `c4e2667f`).

| element | oneDAL form | needed change for the Python ecosystem |
|---|---|---|
| manifest | `repo-eval.yaml` (`schema: repo_manifest.v1`, `guidance.globs`, `image.dockerfile`, `caches`, `network: none`, `entry_points.grade/oracle/static`, `build_systems: [bazel, make]`) | add `env:` (conda/pip lock), `build_systems: [scikit-build, meson-python, setuptools, cmake]`, `test_runner: pytest`, `devices:` block, `numerics:` tolerance policy |
| task keys | `family`, `base`, `grader`, `gate: [strict_pass]`, `hidden_from`, `hidden_files`, `targets`, `regression_targets` (bazel labels), `review_patch`, `gold.json`, `manifest.json` (seeds/decoys), `mutants/*.patch`, `min_kill`, `oracle_targets` | targets become **pytest node IDs / `-k` exprs / `--pyargs` modules**, plus `gtest` filters for C++; add `requires_devices`, `env_overrides` (e.g. `DPNP_TEST_ALL_INT_TYPES=1`), `rebuild: auto/never/always` |
| graders | `test_pass`, `fix_hidden`, `review`, `review_replay`, `mutation`, `bazel_cmd`, `make` | port `test_pass`/`mutation` to pytest; add `numerics`, `differential`, `array_api_delta`, `perf_ab`, `patching`, `coverage_matrix`, `leak`, `docstring` (§5) |
| determinism | rebuild + rerun with `--nocache_test_results`; library sources restored before mutation runs | `pytest -p no:cacheprovider -p no:randomly`, fixed `--hypothesis-seed`, `PYTHONHASHSEED=0`, pinned thread counts, `SYCL_CACHE_PERSISTENT` only for a pre-warmed read-only cache |
| self-check | untouched workspace fails, oracle passes | same; additionally **run the oracle N=3 times** to calibrate numeric/flaky noise and freeze the allowed-flaky node list |
| T0 static | checks every path, label and command named in guidance | adapt: check `pytest` paths, `--pyargs` modules, env vars (`DPNP_TEST_*`, `NO_DPC`, `DALROOT`), conda channel URLs, `meson`/`cmake` options named in AGENTS.md |

The mutation grader uses **hand-written mutant patches**, each proven killable by the reference tests (for example
`tasks/test_add_minkowski/mutants/m1_degree_ignored.patch` … `m7`). Keep that approach for these repos too: Cython,
`.c.src` templates and SYCL kernels are poorly served by automatic mutators (§5.8).

---

## 2. Per-repo profiles

### 2.1 IntelPython/dpctl — SYCL runtime bindings (alive, `master`)

- **Languages** (GitHub bytes): C++ 711k, Cython 410k, Python 349k, CMake 69k, C 41k. Core is `libsyclinterface/`
  (C API over SYCL, C++), wrapped by Cython `dpctl/_sycl_*.pyx`. **`dpctl.tensor` was removed in 0.22**; all of
  it moved to `dpnp.tensor` (dpctl CHANGELOG, gh-2245). Older tensor bugfix PRs (#2219, #2229) therefore only
  exist at bases before the 0.22 cut.
- **Build**: `setup.py` + **scikit-build** + CMake ≥3.29 + Ninja + Cython ≥3.1, compiled with **icpx/DPC++**
  (`{{ compiler('dpcpp') }}` in `conda-recipe/meta.yaml`). Helper `scripts/build_locally.py`. Second recipe
  `conda-recipe-cf/` for conda-forge. A nightly `os-llvm-sycl-build.yml` builds with open-source intel/llvm plus
  the `oclcpuexp` CPU runtime (`OCL_ICD_FILENAMES=libintelocl.so`).
- **Tests**: `python -m pytest --pyargs dpctl` from an empty dir (tests ship in the package).
  `pyproject.toml` has `--strict-markers`, `--junitxml=junit.xml`, and a single marker `broken_complex` that is
  skipped unless `--runcomplex` is passed. A `--no-sycl-interface-test` option exists. C++ gtests live under
  `libsyclinterface/tests/` (CMake). Device fixtures in `dpctl/tests/_device_attributes_checks.py`
  (`valid_filter`, `invalid_filter`, `device_selector`).
- **CI**: `conda-package.yml` builds on `ubuntu-latest` / `windows-latest` for py3.10–3.15 (+3.14t/free-threaded)
  and tests with `SYCL_CACHE_PERSISTENT=1`. `conda-package-cf.yml` installs **`intel-opencl-rt`** "to provide the
  OpenCL CPU device to run the tests on"; Windows registers `intelocl64.dll` under
  `HKLM\SOFTWARE\Khronos\OpenCL\Vendors`. Also `generate-coverage.yaml`, `coverity.yml`,
  `run-tests-from-dppy-bits.yaml`. ASV benchmarks are in `benchmarks/`.
- **CPU-only feasibility**: **yes**. All of CI runs on GitHub-hosted runners with the OpenCL CPU device. Level
  Zero tests (`DPCTL_ENABLE_L0_*`, IPC memory, `test_work_group_memory_opencl.py`) partly depend on GPU drivers.
- **Agent guidance**: none (no AGENTS.md, CLAUDE.md or copilot-instructions).
- **Candidate PRs**
  - **#2382** "Fix bugs in kernel submission and creation" (+172/−11, 6 files, 8 inline review comments): a fix
    that spans Python, Cython and C++, with hidden tests in both pytest (`test_sycl_compiler.py`,
    `test_sycl_kernel_submit.py`) and gtest (`libsyclinterface/tests/test_sycl_queue_submit.cpp`). Good
    *fix-hidden* task that exercises the dual pytest+gtest adapter, and a small review replay.
  - **#2294** "Fix memory leak in `RawKernelArg`" (1-line: `__dealloc` → `__dealloc__` in `_sycl_queue.pyx`, no
    test): a perfect **seeded-bug review** seed (Cython-specific typo that silently disables a destructor) and a
    **leak-grader** fix task.
  - **#2219** "Fix DLPack C-contiguous stride reconstruction" (+83/−9, 5 inline comments, base before 0.22):
    fix-hidden task with an interop oracle (DLPack round-trip vs NumPy `from_dlpack`).
  - **#2229** "Fix `dpt.repeat()` regression for 1D repeats array" (+21/−1): small regression fix with a crisp
    hidden test. Good easy-tier task.

### 2.2 IntelPython/dpnp — NumPy-compatible arrays on SYCL (alive, `master`)

- **Languages**: C++ 5.25 MB (SYCL kernels in `dpnp/tensor/libtensor/include/kernels/**`, `dpnp/backend`),
  Python 4.8 MB, Cython 285k, CMake 183k. Since dpctl 0.22, dpnp hosts `dpnp.tensor` (the former
  `dpctl.tensor`, an array-API library) as well as the NumPy-like `dpnp` namespace.
- **Build**: `setup.py` + scikit-build + CMake + Cython, compiled with icpx (conda `dpcpp_linux-64`), linking
  oneMKL (optionally oneMath for CUDA/HIP via `--target-cuda` / `--target-hip`). `scripts/build_locally.py
  [--oneapi] [--debug] [--onemath] [--skip-editable]`. Conda env specs in `environments/*.yml`. Full builds are
  heavy (5 MB of templated SYCL C++). Expect tens of minutes on a 4-core runner **(unverified)**.
- **Tests**:
  - `pytest -n auto -ra --pyargs dpnp.tests` and separately `--pyargs dpnp.tests.tensor` (`SKIP_TENSOR_TESTS=1`
    turns the latter off).
  - `conftest.py` sets `-m "not slow"` by default, `--strict-markers` and `--tb=short`. Markers: `slow`,
    `multi_gpu`.
  - **Skip tables** keyed by normalized node ID: `skipped_tests.tbl` (12), `skipped_tests_gpu.tbl` (20),
    `skipped_tests_gpu_no_fp64.tbl`, `skipped_tests_cuda.tbl` (662).
  - **dtype-matrix env knobs** in `dpnp/tests/config.py`: `DPNP_TEST_ALL_INT_TYPES`, `DPNP_TEST_FLOAT_16`,
    `DPNP_TEST_COMPLEX_TYPES`, `DPNP_TEST_BOOL_TYPES`.
  - Tolerance helper `assert_dtype_allclose(dpnp_arr, numpy_arr, factor=8)`: tolerance =
    `factor × max(finfo(dpnp).resolution, finfo(numpy).resolution)`, exact match for non-inexact dtypes, dtype
    kind-only check on devices without fp64.
- **Reused test suites**:
  - `dpnp/tests/third_party/cupy/**`: CuPy's test suite, ported.
  - `dpnp/tests/third_party/numpy_ext`.
  - `tests_external/numpy/runtests.py`: NumPy's own test suite run under dpnp, with
    `skipped_tests_numpy.tbl` (2,826 lines) and `skipped_tests_numpy_aborted.tbl` (789). It is not wired into
    current workflows.
- **Array API conformance**: CI job `array-api-conformity` clones `data-apis/array-api-tests` and runs
  `ARRAY_API_TESTS_MODULE=dpnp ARRAY_API_TESTS_VERSION=2025.12 pytest --json-report --disable-deadline
  --skips-file .github/workflows/array-api-skips.txt` (1 skip). It is **report-only** (`|| true`); pass, fail and
  skip counts are posted as a PR comment.
- **CI**: `conda-package.yml` builds and tests on `ubuntu-latest` (py3.12/3.13/3.14/3.14t) and Windows, with
  `nick-fields/retry` on test steps (flake tolerance). Windows test env installs `intel-opencl-rt=*=intel_*`. Also
  `cron-run-tests.yaml`, `conda-build-test.yml`, `generate_coverage.yaml`, `check-onemath.yaml`. ASV `benchmarks/`
  holds dpnp-vs-numpy elementwise, linalg, random and dpBench workloads; README says device is chosen via
  `ONEAPI_DEVICE_SELECTOR`.
- **CPU-only feasibility**: **yes** for everything except the GPU skip tables, no-fp64 paths, `multi_gpu`, and
  the CUDA/HIP backends.
- **Agent guidance**: none.
- **Candidate PRs** (very rich vein — dozens of small, test-backed numerical fixes in 2026)
  - **#3043** "Fix `dpnp.linspace` returning NaN for equal infinite endpoints" (+108/−18; Python + SYCL kernel
    `constructors.hpp`; hidden tests in `test_arraycreation.py` and `tests/tensor/test_usm_ndarray_ctor.py`).
    Numerical-edge-case fix that needs a C++ rebuild. NumPy is a sound oracle here.
  - **#3081** "Fix `dpnp.median`/`nanmedian` result shape for empty and size-1 kept dimensions" (+51/−2,
    Python-only `dpnp_utils_statistics.py`). Fix-hidden task that runs in **Python-overlay mode** (no rebuild),
    so it is cheap and good for many reps.
  - **#3063** "Fix row corruption in batched multi-level cumulative scan" (+22/−2 in `accumulators.hpp`,
    17-line hidden test): silent-wrong-result bug in a SYCL kernel. Hard tier, and a strong **differential
    (dpnp vs numpy)** grader case.
  - **#2914** "Fix `dpnp.tensor.acosh` handling of complex(±0, NaN)" (4-line kernel deletion, **16 inline review
    comments**). Review replay where reviewers note that "conda-forge numpy gives `nan, nan` while wheels give
    `0, nan`… better not to compare it to NumPy at all". Use it as the canonical example of **spec-oracle beats
    NumPy-oracle** (§5.1).
  - **#3045** "Fix `dpnp.ndarray.flat` indexing edge cases" (+699/−77, **39 inline comments**, 28 reviews).
    Best **review-replay** candidate in the set. Reviewer comments are behavioral and come with runnable NumPy
    reproducers (OOB tuple index, scalar key not raising `ValueError`, missing `idx.ndim > 1` check, `dpnp.put`
    overlap semantics, "bypasses compute-follows-data"), so gold comments can become **executable probes**
    (§5.10).
  - Also good: #3055 (non-0/1 bool bytes, 12 files, 18 comments), #3041 (`insert` OOB negative indices,
    Python-only), #3021 (abort in `all`/`any` over empty axis), #3022 (`top_k` k=0 abort), #3068 (buffer offset
    ignored, 11 comments).

### 2.3 IntelPython/mkl_fft — NumPy/SciPy FFT on oneMKL DFTI (alive, `master`)

- **Languages**: Python 189k, Cython 31k (`_pydfti.pyx`), C 16k (`src/*.c.src` templates), Meson.
- **Build**: `pyproject.toml` + **meson-python** + Cython + `mkl-devel` (pip or conda); compiler icx or clang
  (CI has `build-with-clang.yml` and `build-with-standard-clang.yml`). `build_pip.yml` runs
  `pip install -e ".[test]" --no-build-isolation` with NumPy pre-releases.
- **Tests**: `pytest mkl_fft/tests`. Files: `test_fft1d.py`, `test_fftnd.py`, `test_interfaces.py`,
  `test_patch.py`, `test_dispatch_equivalence.py`, plus **vendored upstream suites** in
  `tests/third_party/numpy/test_fft.py` and `tests/third_party/scipy/{test_basic,test_multithreading}.py`.
  Tolerances are hand-set per test (`rtol=1e-7, atol=2e-12` for double, `2e-6` for single).
- **CI**: conda (Intel channel and conda-forge flavors), pip, clang variants, coverity. ASV in `benchmarks/`.
  Pure CPU (oneMKL), no SYCL device, so **fully feasible on GitHub-hosted runners**.
- **Agent guidance**: **yes**. Root `AGENTS.md` plus nested `AGENTS.md` in `.github/`, `mkl_fft/`,
  `mkl_fft/interfaces/`, `mkl_fft/src/`, `mkl_fft/tests/`, both conda recipes, `_vendored/` and `benchmarks/`.
  `.github/copilot-instructions.md` sets precedence "copilot-instructions > nearest AGENTS > root AGENTS" and
  rules: "bug fixes require a regression test", "edit `*.c.src`, not generated C", "preserve NumPy/SciPy FFT API
  compatibility", "never invent versions/CI matrices". This makes it a strong arms-comparison repo (several of
  those rules can be graded statically, §5.11).
- **Candidate PRs**
  - **#370** "fix: norm scaling" (+511/−6, **15 inline comments**). Two silent mis-scaling bugs in the N-D API
    (subset-axes `norm`), hidden test `test_dispatch_equivalence.py` (+483). The PR body says it was found by
    "iterative agentic looping dev experimentation", so it directly fits a numerical-accuracy fix task, and its
    test is a differential oracle (root API vs `interfaces.numpy_fft` vs `numpy.fft`).
  - **#373** "Fix possible memory leaks in backend" (`_pydfti.pyx`, `mklfft.c.src`, `multi_iter.h`). Found
    during free-threading review, no direct test. Good for **review replay** (5 comments) or a **leak-grader**
    fix.
  - **#358** "Fix macOS rpath: one `-rpath` per path" (meson build fix). A **build**-family task.

### 2.4 IntelPython/mkl_random — NumPy RandomState on oneMKL VSL (alive, `master`)

- **Languages**: Cython 264k (`mklrand.pyx`), C++ 82k (`src/mkl_distributions.cpp`), C 27k, Python 174k, Meson.
- **Build**: meson-python + Cython + mkl-devel; same clang/icx CI matrix as mkl_fft.
- **Tests**: `pytest mkl_random/tests`. Files: `test_random.py`, `test_patch.py`, `test_freethreading.py`,
  `test_cli.py`, plus **vendored NumPy RNG suites** `third_party/test_numpy_random.py` and
  `test_numpy_regression_randomstate.py`. Results are stream-exact per BRNG, so these tests are **bitwise**, not
  tolerance-based. Statistical properties need moment/KS tests with fixed seeds. CPU only, fully feasible.
- **Agent guidance**: none. **ASV**: yes.
- **Candidate PRs**
  - **#172** "64-bit randint could return `high`; correct the integer fills" (+130/−189 in
    `mkl_distributions.cpp`, 31-line hidden test, **19 inline comments**). Off-by-one in a masked rejection
    sampler. Doubles as a fix-hidden task (hidden test: `randint(lo, hi)` never returns `hi` across dtypes) and a
    review replay (comment: "near-twin `irk_discrete_uniform_long_vec` keeps the old … loop" is exactly the
    "find the sibling bug" skill).
  - **#167** "Fix unreachable scalar fast-path in `RandomState.uniform`" (mis-indentation in `.pyx`;
    `uniform(0,1)` returned a 0-d ndarray instead of `float`). Good **API-compat grader** case: return-type
    parity with `numpy.random.uniform`.
  - **#177** "Fix out-of-range integer `brng` reading past `brng_list`" (`brng=11` read uninitialized memory).
    Seeded-bug or fix task with a crisp hidden test.
  - **#156** "Fix constant integer overflow in `irk_rand_uint32_vec`" (2 lines). Seeded-bug candidate.

### 2.5 IntelPython/mkl_umath — NumPy ufunc loops on MKL VM/SVML (alive, `main`)

- **Languages**: Python 355k (mostly generated docstrings/codegen), C 25k (`src/mkl_umath_loops.c.src`,
  `ufuncsmodule.c`), Cython 14k (`_patch_numpy.pyx`), Meson 7k.
- **Build**: meson-python, `meson.options`, icx (Intel-only flags gated for clang); codegen via
  `generate_umath.py`.
- **Tests**: `mkl_umath/tests/test_basic.py` loops over every ufunc in `mkl_umath._ufuncs` and compares with
  NumPy using `np.allclose` (default rtol 1e-5 / atol 1e-8, no ULP bound), with `np.random.seed(42)` inputs
  drawn from `uniform(low, high)`. Also `test_patching.py`, `test_freethreading.py`, `test_cli.py`. The current
  tolerance is loose and has **no special-value coverage** (NaN, ±0, ±inf, subnormals), which is where a ULP and
  special-value grader adds value (#269 below was exactly such a bug).
- **CI**: conda (Intel and conda-forge), pip, clang variants, coverity. CPU only, fully feasible. ASV yes.
- **Agent guidance**: **yes** (root, `.github/`, `mkl_umath/`, `src/`, `tests/`, recipes, `_vendored/`, plus
  `copilot-instructions.md`).
- **Candidate PRs**
  - **#269** "BUG: fix `absolute()` returning −NaN for +NaN on scalar path" (1-line `in1 > 0 ? in1 : -in1` →
    `fabs`, hidden test +13). Ideal **seeded-bug** (NaN-comparison classic) and **special-value grader** task.
    NumPy fixed the same bug upstream.
  - **#263** "Avoid leaking module object on numpy C-API import failure" (`import_array()` macro returns early
    after `PyModule_Create`). Review-replay or seeded-bug (CPython C-API refcount idiom), no test.
  - **#275** "Fix `libmkl_umath_loops.so` ImportError under meson-python ≥ 0.22". A **build** task (break the
    env with meson-python 0.22 and ask the agent to make the wheel importable).

### 2.6 IntelPython/numba-dpex — **archived 2025-09-26**

- `archived: true`, last push 2025-09-26. Python 890k, C 83k (kernel API, SPIR-V via Numba). CI used
  `ONEAPI_DEVICE_SELECTOR=opencl:cpu` with `NUMBA_DPEX_BUILD_KERNEL_OPTIONS=-cl-opt-disable` and smoke-tested
  import with `ONEAPI_DEVICE_SELECTOR=unknown:unknown`.
- **Recommendation**: exclude from the task pool. Its CI pattern (CPU OpenCL selector + no-device import smoke
  test) is still a useful template for the device adapter.

### 2.7 uxlfoundation/scikit-learn-intelex (sklearnex + onedal bindings + daal4py)

- **Languages**: Python 2.2 MB, C++ 520k (`onedal/**` pybind11, `src/`), Cython 17k (daal4py), CMake. Four
  layers: `sklearnex/` (sklearn API, patching, dispatch) → `daal4py/` (Cython, legacy, model builders) and
  `onedal/` (pybind11, device offload) → oneDAL C++.
- **Build**: `setup.py` (setuptools + Cython + pybind11 + CMake) with env vars `DALROOT` (required),
  `NO_DPC` (skip DPC++ GPU bindings), `NO_DIST` (skip MPI/SPMD), `NO_STREAM`, `MPIROOT`. oneDAL comes from
  `dal-devel` (pip/conda) or the **oneDAL nightly artifact** (CI job `onedal_nightly` downloads `__release_lnx`
  from the oneDAL repo). Conda recipe in `conda-recipe/`.
- **Tests** (5 roots):
  - `pytest tests/` (legacy and examples), `pytest daal4py`, `pytest sklearnex`, `pytest onedal`.
  - `.ci/scripts/test_global_patch.py`.
  - SPMD via `mpirun -n 4 python -m pytest --pyargs sklearnex.spmd --with-mpi`.
  - **scikit-learn's own test suite** run against patched sklearn: `.ci/scripts/run_sklearn_tests.sh [cpu|gpu]`
    → `select_sklearn_tests.py` (a map of sklearn test files) with deselection from **`deselected_tests.yaml`**
    (655 lines, version-conditional e.g. `<1.7`, flags `--public --gpu --preview`).
  - Device/dataframe parametrization is `onedal/tests/utils/_dataframes_support.get_dataframes_and_queues
    (dataframe_filter_, device_filter_="cpu,gpu")` (numpy, pandas, dpnp, dpctl, array_api_strict, torch).
  - Patching verification via **log records**: `sklearnex: running accelerated version on CPU|GPU` vs
    `fallback to original Scikit-learn` (`sklearnex/_utils.py:79-92`, asserted with `caplog` in
    `sklearnex/tests/test_patching.py`).
  - `test_standard_estimator_signatures`, `test_docstring_patching_match`, `test_patch_map_match`.
  - `test_run_to_run_stability.py` asserts **bitwise** reproducibility (`assert_allclose(rtol=0, atol=0)`).
  - Default timeout 170 s/test (480 s for GBT) per `tests/AGENTS.md`.
- **CI**: GitHub `ci.yml` on `ubuntu-26.04` / `windows-2025`, matrix (py3.10 + sklearn 1.6, 3.13 + 1.8,
  3.14 + 1.9) with `NO_DIST=1` and pip `dpctl`/`dpnp` installed for DPC-capable Pythons. A `test_uxl` job runs
  array-API frameworks (`pytorch,numpy`) on `cpu`. Legacy Azure pipelines in `.ci/pipeline/*.yml` use
  `vmImage: ubuntu-24.04 / windows-2022` (hosted). GPU validation is not in public CI (presumably internal
  **(unverified)**).
- **CPU-only feasibility**: **yes** (`NO_DPC=1 NO_DIST=1`), given a prebuilt `dal-devel`. GPU paths (dpnp/dpctl
  queues on GPU, `--gpu` deselections) need an Intel GPU.
- **Agent guidance**: **richest of the set**. `AGENTS.md` at root, `.ci/`, `tests/`, `examples/`, `onedal/`,
  `doc/`, `sklearnex/`, `daal4py/`, `src/`, and `.github/instructions/{general,sklearnex,daal4py,onedal,src,tests,
  build-config}.instructions.md`.
- **Candidate PRs**
  - **#3357** "Fall back to sklearn for forest predictions on NaN data" (+73/−2; hidden test in
    `ensemble/tests/test_forest.py`, 4 comments). Graded by the **patching grader**: correct output **and** a log
    showing fallback for NaN input / acceleration otherwise.
  - **#3295** "dtype of LogisticRegression coefficients hardcoded to float64" (+28/−6). A **dtype-coverage**
    fix-hidden task (float32 in → float32 coef, matching sklearn ≥1.8 behavior).
  - **#3348** "fix array_api_strict and various deselections" (−17 lines from `deselected_tests.yaml`, C++ fix in
    `onedal/datatypes/table.cpp` and `dlpack/data_conversion.cpp`). The hidden tests are **upstream sklearn tests
    that were deselected** (§3.2 "deselection-as-hidden-test"). Needs a C++ rebuild.
  - **#3182** "Implement namespace checks, 'everything follows X', fix oneDAL model re-creation in SVMs"
    (+881/−142, **43 inline comments**). Large **review replay**. #3198 (KMeans predict on sparse after dense fit,
    +62/−15) is a smaller fix-hidden companion.
  - **#3467** "index tree counts by class when padding treelite models" (`daal4py/mb/gbt_convertors.py`, +51-line
    hidden test in `tests/test_model_builders.py`). Fix task whose oracle is a **cross-library differential**
    (XGBoost/LightGBM predictions vs converted daal4py model).

### 2.8 IntelPython/daal4py

`gh api repos/IntelPython/daal4py` returns the scikit-learn-intelex metadata (same stars, same push time): the repo
was renamed/transferred. daal4py lives at `scikit-learn-intelex/daal4py/` with its own `AGENTS.md` and
`.github/instructions/daal4py.instructions.md`. Treat its tasks as `repo: scikit-learn-intelex`,
`test_root: daal4py` (`pytest --verbose daal4py`, `tests/test_daal4py_examples.py`, model builders).

### 2.9 dmlc/xgboost (Intel CPU and SYCL contributions)

- **Languages**: C++ 3.6 MB, Python 1.6 MB, CUDA 1.3 MB, R, Scala, Java. Intel work is (a) the **SYCL plugin**
  `plugin/sycl/**` (predictor, tree updaters, objectives, `device_manager`) with gtests
  `tests/cpp/plugin/test_sycl_*.cc` and Python tests `tests/python-sycl/*.py`, and (b) CPU performance work in
  `src/` by Intel engineers (e.g. `razdoburdin`).
- **Build**: CMake + Ninja (`-DGOOGLE_TEST=ON -DPLUGIN_SYCL=ON`). The Python package uses
  **scikit-build-core** (`python-package/pyproject.toml`).
- **Tests**: `./build/testxgboost` (gtest) plus `pytest tests/python*`. Heavy use of **hypothesis**
  (`tests/python-sycl/test_sycl_updaters.py` uses `@given` with `settings(deadline=None)`), and the shared
  `xgboost.testing` module (`updater.py`, `predict.py`, `continuation.py`, `with_skl.py`, …) provides reusable
  correctness checks.
- **CI**:
  - `sycl_tests.yml` "XGBoost CI (oneAPI)": `runs-on: ubuntu-latest`, micromamba env
    `ops/conda_env/linux_sycl_test.yml` (conda-forge + Intel channel: `dpcpp_linux-64>=2024.2.1`,
    `onedpl-devel`, `intel-openmp`, gtest, hypothesis, dask), then `ops/pipeline/build-test-sycl.sh` (CMake
    build, `testxgboost`, `pip install python-package`, `pytest tests/python-sycl`). This runs on a hosted runner,
    so it uses a CPU SYCL device.
  - Main CI (`main.yml`) uses **RunsOn** self-hosted AWS runners (`runs-on=${{ github.run_id }}`) for CPU and
    CUDA.
- **CPU-only feasibility**: **yes** for C++ CPU, Python CPU and the SYCL plugin on a CPU device.
  **fp32-only-device paths** (iGPU) need GPU hardware.
- **Agent guidance**: none in the repo.
- **Candidate PRs**
  - **#12041** "sycl. Fix multiclass objective calculation" (implements `Mean` for SYCL devices; +39/−4, **no
    test added**). A **test-writing (mutation)** task: "add tests for `common::Mean` on SYCL" with mutants in
    `plugin/sycl/common/stats.cc`.
  - **#11808** "Make block_size of BuildHistKernel adaptive" (Intel, +277/−23, **27 inline comments**, claims
    "up to 2× on epsilon"). Use for **review replay** and a **perf-AB** feature task (gate: tests pass; score:
    speedup on a fixed synthetic dataset).
  - **#11519** "optimize CPU inference with Array-Based Tree Traversal" (Intel, `array_tree_layout.h` +226,
    `cpu_predictor.cc`, gtest +75, **27 inline comments**). Perf feature task with a strict
    **prediction-equivalence** gate (bitwise or ≤1 ULP vs base predictor) plus speedup score. Also good for
    review replay.
  - **#12604** "Fix Driver potentially skipping valid split candidates" (C++ `src/tree/driver.h`, gtests
    `test_driver.cc` / `test_empty_pop.h`) and **#12541** "Fix XGBClassifier refit after class-count changes"
    (Python, +31-line hidden test in `test_with_sklearn.py`). Clean non-SYCL fix-hidden tasks. #12618
    (`-inf` → `-Infinity` in JSON dumps) is a nice small one.
  - **#11524** "SYCL. Fix training continuation for fp32 devices" (1 file, summation reordering on iGPU only). An
    example of a task that is **not reproducible on CPU** (keep it out of the CPU pool and tag
    `requires_devices: [gpu_no_fp64]`).

---

## 3. Cross-repo synthesis

### 3.1 Summary matrix

| repo | lang core | build | test entry | device | CPU-only on GH-hosted | agent docs | ASV | upstream suite reuse |
|---|---|---|---|---|---|---|---|---|
| dpctl | C++/Cython | scikit-build+CMake, icpx | `pytest --pyargs dpctl` + gtest | SYCL | yes (OpenCL CPU) | none | yes | — |
| dpnp | SYCL C++/Py | scikit-build+CMake, icpx, oneMKL | `pytest -n auto --pyargs dpnp.tests[.tensor]` | SYCL | yes (OpenCL CPU), minus GPU tables | none | yes | CuPy tests, NumPy suite, array-api-tests |
| mkl_fft | Py/Cython/C | meson-python, mkl-devel | `pytest mkl_fft/tests` | CPU | yes | **yes** | yes | NumPy + SciPy FFT tests |
| mkl_random | Cython/C++ | meson-python | `pytest mkl_random/tests` | CPU | yes | none | yes | NumPy random tests |
| mkl_umath | C/Cython | meson-python, icx/clang | `pytest mkl_umath/tests` | CPU | yes | **yes** | yes | — |
| numba-dpex | — | — | — | — | archived | — | — | — |
| sklearnex (+daal4py, onedal) | Py/C++ | setuptools+CMake, DALROOT | 4 pytest roots + sklearn suite | CPU/SYCL | yes (`NO_DPC=1 NO_DIST=1`) | **yes (richest)** | external (scikit-learn_bench) | **sklearn test suite** + estimator checks |
| xgboost | C++/Py | CMake+Ninja, scikit-build-core | gtest + pytest + hypothesis | CPU/CUDA/SYCL | yes for CPU+SYCL-on-CPU | none | — | `xgboost.testing` |

### 3.2 Patterns the framework should exploit

1. **Upstream suites as hidden tests.** dpnp runs CuPy/NumPy tests, mkl_fft vendors NumPy/SciPy FFT tests,
   mkl_random vendors NumPy RNG tests, and sklearnex runs sklearn's suite. A fix that **removes lines from a skip
   or deselect list** (`deselected_tests.yaml`, `skipped_tests*.tbl`, `array-api-skips.txt`) gives
   hidden tests for free: the grader runs exactly the removed node IDs (plus the remaining list as regression).
   Example: sklearnex#3348.
2. **Skip/deselect lists are an attack surface.** An agent can "fix" a failure by adding a node ID to a skip
   table. Graders must restore these files from base (like oneDAL restores library sources), or fail the gate if
   they grow (analogous to "do not weaken or delete existing tests").
3. **Tests ship inside the installed package** (`--pyargs`). Hidden-test injection must target the **import
   path actually used** (editable install → source tree; conda/wheel install → `site-packages/dpnp/tests`).
   The adapter resolves `python -c "import dpnp.tests, os; print(os.path.dirname(dpnp.tests.__file__))"`.
4. **Python-only fixes can skip the rebuild.** Many dpnp/sklearnex fixes touch only `.py`. Build the base once
   (cached wheel or conda package in the image), then overlay the agent's `.py` files with an editable install or
   a `sitecustomize` path shim. The grader computes `rebuild = any(changed file matches *.cpp|*.hpp|*.pyx|*.pxd|
   *.c.src|CMakeLists.txt|meson.build)` and falls back to a full rebuild only then. This turns a 30–60 min task
   into a ~1–3 min one **(timings unverified)**.
5. **Numerical tolerance policy is repo-specific and already codified.** dpnp uses `assert_dtype_allclose`
   (resolution × factor 8), mkl_fft uses hand-picked `rtol/atol`, mkl_umath `np.allclose` defaults, sklearnex
   `assert_allclose` with per-algorithm tolerances plus bitwise run-to-run stability, and mkl_random is
   bit-exact. The numerics grader should **reuse the repo's helper by import** rather than invent tolerances.
6. **The reference oracle can be wrong.** dpnp#2914 reviewers found NumPy's result differs between conda-forge
   and PyPI builds. Graders need an oracle hierarchy: spec special-case table > high-precision reference
   (mpmath) > NumPy/SciPy/sklearn, and the NumPy build must be pinned in the image.
7. **Free-threading (cp314t) is a hot topic.** mkl_fft#357, mkl_random#159, mkl_umath#255 and dpctl added
   free-threaded builds, and several leaks were found during that review (mkl_fft#373). Thread-safety tasks are a
   natural new family (§5.12).

---

## 4. (a) Adapters the framework needs

Each adapter is a small module selected by `repo-eval.yaml` and overridable per task. Proposed manifest:

```yaml
schema: repo_manifest.v2
id: dpnp
source: {url: https://github.com/IntelPython/dpnp}
eval_dir: .agent_evals
guidance: {globs: ["**/AGENTS.md", ".github/instructions/*.md", ".github/copilot-instructions.md"], also_scan: ["CONTRIBUTING.md"]}
env:
  kind: conda                        # conda | pip | uv
  lockfile: .agent_evals/env/linux-64.conda-lock.yml   # explicit, no solver at run time
  channels: [https://software.repos.intel.com/python/conda, conda-forge]
  exclude_packages: [dpnp]           # never preinstall the package under test (leak guard)
build:
  system: scikit-build               # scikit-build | meson-python | setuptools | cmake | scikit-build-core
  command: "python scripts/build_locally.py --oneapi --skip-editable"
  python_overlay: true               # .py-only diffs reuse the cached base build
  rebuild_globs: ["**/*.cpp", "**/*.hpp", "**/*.pyx", "**/*.pxd", "**/CMakeLists.txt"]
  ccache: /cache/ccache
test:
  runner: pytest
  import_mode: pyargs                # pyargs | path
  package: dpnp.tests
  base_args: ["-p", "no:cacheprovider", "-p", "no:randomly", "-ra", "--junitxml={out}/junit.xml"]
  marker_expr: "not slow and not multi_gpu"
  env: {DPNP_TEST_ALL_INT_TYPES: "1", PYTHONHASHSEED: "0"}
  protected_files: ["dpnp/tests/skipped_tests*.tbl", "tests_external/*.tbl", ".github/workflows/array-api-skips.txt"]
devices:
  default: "opencl:cpu"
  matrix: {cpu: "opencl:cpu", gpu: "level_zero:gpu", gpu_ocl: "opencl:gpu"}
  probe: "python -m dpctl -f"
numerics: {helper: "dpnp.tests.helper:assert_dtype_allclose", reference: numpy}
image: {dockerfile: .agent_evals/Dockerfile, network: none, caches: [{name: sycl-jit, path: /cache/sycl}, {name: ccache, path: /cache/ccache}]}
build_systems: [scikit-build, cmake]
```

### A1. Env adapter (conda / pip / uv)

- **conda**: build the image from an explicit **conda-lock** file per task base range, using channels Intel and
  conda-forge with `--override-channels`. Install `intel-opencl-rt` for the CPU device (dpnp's Windows job pins
  `intel-opencl-rt=*=intel_*`). Never install the package under test from a channel (`exclude_packages`), or a
  later release containing the fix leaks in.
- **pip/uv**: wheelhouse pre-downloaded at image build time, `uv pip install --offline --no-index
  --find-links /wheelhouse`. Intel PyPI index `https://software.repos.intel.com/python/pypi` for
  `mkl-devel`, `dpcpp-cpp-rt`, `intel-sycl-rt`, `dal-devel`, and public PyPI for `intel-opencl-rt`.
- **Verified**: the PyPI wheel `intel_opencl_rt-2026.1.2-…-manylinux_2_28_x86_64.whl` (204 MB) contains
  `data/lib/libintelocl.so`, `data/lib/libOpenCL.so*`, `data/lib/cl.cfg` and
  `data/etc/OpenCL/vendors/intel-cpu.icd`. pip places these under `$VIRTUAL_ENV/lib` and
  `$VIRTUAL_ENV/etc/OpenCL/vendors`, so the adapter must export
  `OCL_ICD_VENDORS=$VIRTUAL_ENV/etc/OpenCL/vendors` (or `OCL_ICD_FILENAMES=$VIRTUAL_ENV/lib/libintelocl.so`) and
  `LD_LIBRARY_PATH`. That dpctl then lists the CPU device from a pip-only env is **unverified** (not run). The
  conda path is proven by the repos' own CI.
- Pin **NumPy build provenance** (conda-forge vs wheel vs Intel), because reference results differ (dpnp#2914).
- Thread pinning for reproducibility and perf: `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `TBB_NUM_THREADS` (or
  `taskset`), `MKL_CBWR=COMPATIBLE` when bitwise-stable MKL results are required.

### A2. Build adapters

| adapter | repos | command | notes |
|---|---|---|---|
| `scikit-build` (legacy setup.py) | dpctl, dpnp | `python scripts/build_locally.py --oneapi` or `pip install -e . --no-build-isolation` | needs icpx (`dpcpp_linux-64`), CMake ≥3.29, Ninja; keep the `_skbuild` dir in a cache to make incremental rebuilds cheap |
| `meson-python` | mkl_fft, mkl_random, mkl_umath | `pip install -e . --no-build-isolation` (editable meson rebuilds on import) | compiler `CC=icx` or clang; mkl-devel; regenerate `.c.src` templates; mkl_umath#275 shows meson-python version sensitivity |
| `setuptools-custom` | scikit-learn-intelex | `DALROOT=$CONDA_PREFIX NO_DPC=1 NO_DIST=1 python setup.py build_ext --inplace` (or `pip install -e .`) | oneDAL from `dal-devel`; `NO_DPC=1` drops the SYCL toolchain, a big image-size win for CPU tasks |
| `cmake` (+ctest/gtest) | xgboost C++, dpctl `libsyclinterface` | `cmake -GNinja -DGOOGLE_TEST=ON [-DPLUGIN_SYCL=ON]` + `ninja` + `./testxgboost --gtest_filter=…` | gtest filter is the target unit; SYCL plugin build needs dpcpp from conda |
| `scikit-build-core` | xgboost python-package | `pip install -v ./python-package` | reuses `build/` lib via `use_system_libxgboost` option **(unverified for this version)** |
| `python-overlay` (meta) | any | no build; reuse base artifact | selected automatically when the diff touches only `.py` |

The build grader (`family: build`) checks the **exact command** like oneDAL's `bazel_cmd`, generalized to
`expect: {cmd_regex, artifact_glob, smoke: "python -c 'import dpnp; dpnp.ones(3).sum()'"}`.

### A3. pytest adapter

- **Targets** are node IDs or `{module, k, m}` triples. Results come from `--junitxml`, parsed into per-node
  pass, fail, skip and xfail.
- **Strict pass** means all hidden node IDs pass, no regression-suite node that passed at base fails, and the
  number of **skips did not grow** for regression nodes (catches `pytest.skip` insertion).
- Marker handling: honor repo defaults (dpnp `-m "not slow"`), expose `marker_expr`, and recognize custom
  options (dpctl `--runcomplex`, sklearnex `--with-mpi`).
- **Node-ID normalization** (dpnp `normalize_test_name` maps `--pyargs` IDs to `tests/...`) so hidden lists and
  skip tables are comparable across invocation styles.
- Parallelism: `pytest-xdist -n auto` is allowed for regression suites, but hidden tests run with `-n0` and
  `-p no:randomly` for determinism.
- **Hypothesis**: `--hypothesis-seed=<task seed>`, profile with `derandomize=True`, `deadline=None`, fixed
  `max_examples`. Store `.hypothesis/` examples DB per task (read-only) to replay known failing examples.
- **Timeouts**: `pytest-timeout` per node (sklearnex 170 s default, 480 s GBT), plus a global wall clock.
- **Retry policy**: dpnp CI wraps tests in `nick-fields/retry`. The grader must **not** retry hidden tests.
  Instead, the self-check runs oracle×3 and records a frozen `flaky_allow` list.

### A4. SYCL device-matrix adapter

- Use `ONEAPI_DEVICE_SELECTOR` (`opencl:cpu`, `level_zero:gpu`, `opencl:gpu`, `*:cpu`, `!opencl:*`, …).
  **`SYCL_DEVICE_FILTER` is deprecated** in recent DPC++. Do not emit it, but translate it if old task bases'
  test code reads it.
- **Probe** at grade start: `python -m dpctl -f` (dpctl's `run_test.sh` uses it) or `sycl-ls`. Record a
  capability vector `{backend, device_type, fp64, fp16, atomic64, usm_shared, max_wg}` into `grade.json`.
- Task key `requires_devices: [cpu]` (default) | `[gpu]` | `[gpu_no_fp64]` | `[multi_gpu]`. If unmet, mark the
  run **`infeasible`** (not failed). Optionally, if `cpu_fallback: true`, run the CPU subset of hidden tests and
  report `partial_device_coverage`.
- **JIT cache**: OpenCL CPU JIT compiles every kernel. Set `SYCL_CACHE_PERSISTENT=1` with
  `SYCL_CACHE_DIR=/cache/sycl` pre-warmed from the oracle run (read-only for the agent, so JIT time does not
  count against the agent's budget). Grading must use a **fresh copy** of the cache to avoid stale-kernel false
  passes after a kernel edit. A safer default is no persistent cache for hidden-test runs on kernel-touching
  diffs.
- No-device smoke: `ONEAPI_DEVICE_SELECTOR=unknown:unknown python -c "import pkg"` (numba-dpex CI pattern)
  verifies the package still imports on machines with no device.
- Windows: register `intelocl64.dll` under `HKLM\SOFTWARE\Khronos\OpenCL\Vendors` (dpctl/dpnp CI scripts).

### A5. C++ adapters (CMake/ctest/gtest; bazel/make kept for oneDAL)

- gtest targets: `{"binary": "build/testxgboost", "filter": "Sycl*Mean*:*Driver*"}`. Parse `--gtest_output=xml`.
- Restore-library-sources-before-mutation logic is identical to oneDAL's, with `is_test_file` extended to
  `tests/**/*.py`, `**/tests/**`, `tests/cpp/**`.
- Format checks: clang-format for C++, `ruff`/`black` and `cython-lint` (mkl_* use cython-lint in `pyproject.toml`)
  as `format_ok` metrics, not gates.

---

## 5. (b) Science-specific grader types

All are deterministic (no LLM judge). They come in two kinds: gates (pass/fail) and scores (continuous).

### 5.1 `numerics` — numerical-accuracy grader

- **Inputs**: a task-local corpus `numerics/corpus.npz` (generated once at task creation, checked in, so it is
  deterministic). It holds normal ranges per dtype plus **special values**: ±0, ±inf, NaN (both sign bits and
  payloads), subnormals, ±max/min, near-branch-cut complex values, empty, 0-d, size-1, non-contiguous, negative
  strides, non-zero USM offsets (dpnp#3068, #3037), and Fortran order.
- **Oracle hierarchy** (first available wins, recorded per check):
  1. **Spec special-case table** (array API special cases, C99 Annex G for complex). Exact match including NaN
     sign rules where the spec defines them.
  2. **High-precision reference** (mpmath at 100+ digits, rounded to target dtype) → **ULP error**. Gate:
     `max_ulp <= bound[dtype][func]`, with bounds taken from MKL VM accuracy modes (HA/LA/EP) or the repo's
     documented accuracy.
  3. **Library reference** (NumPy / SciPy / sklearn pinned build) → `rtol/atol` from the repo helper
     (`assert_dtype_allclose(factor=8)` for dpnp, per-test tolerances for mkl_fft).
- **Score**: max and p99 ULP, count of special-value mismatches. **Gate**: no regression vs base on any corpus
  element (base error ≤ x ⇒ agent error ≤ x + ε), plus the target cases fixed.
- Fits dpnp#3043 (linspace inf), #2914 (acosh), mkl_umath#269 (abs NaN sign), mkl_fft#370 (norm scaling).

### 5.2 `differential` — cross-backend consistency grader

- **dpnp vs numpy**: for a list of APIs × corpus × dtype matrix × `{C, F, strided}` layouts, compare
  `dpnp.asnumpy(f_dpnp(x))` to `f_numpy(x)` using the 5.1 oracle tolerance. Also compare **dtype result**
  (type promotion parity, honoring `has_aspect_fp64`), **shape**, and **exception type** (`ValueError` vs
  `IndexError`, from the dpnp#3045 review). Exceptions are first-class outcomes. Dozens of 2026 dpnp fixes are
  exactly "raise like NumPy" or "don't abort".
- **dpnp.tensor vs array_api_strict**: same harness for the array-API namespace.
- **mkl_fft / mkl_random / mkl_umath vs NumPy/SciPy**: interface modules (`mkl_fft.interfaces.numpy_fft`,
  `scipy_fft`) vs `numpy.fft`/`scipy.fft`. mkl_random is compared **distributionally** (fixed seed, KS or
  chi-square p > threshold with fixed sample sizes, deterministic given seed), never bitwise against NumPy.
- **sklearnex vs stock sklearn**: fit the same estimator patched and unpatched on fixed datasets (sklearn
  `make_*` with `random_state`) and compare predictions, scores and fitted attributes with per-estimator
  tolerances (e.g. KMeans inertia rtol 1e-5, LinearRegression coef rtol 1e-6, LogisticRegression looser for
  solver differences). Attribute presence and dtype must match too (sklearnex#3432 `solver_` attr, #3295 coef
  dtype).
- **xgboost**: prediction equivalence between base and agent build for perf refactors (bitwise for
  `cpu_predictor`, ≤ 1 ULP when summation order legitimately changes). For the SYCL plugin, compare
  `device="sycl:cpu"` against `device="cpu"` within the tolerance used by `tests/python-sycl`.
- **daal4py model builders**: XGBoost/LightGBM/CatBoost prediction vs converted model (sklearnex#3467).

### 5.3 `array_api_delta` — array-API conformance delta

- Pin `data-apis/array-api-tests` commit, `ARRAY_API_TESTS_VERSION` (dpnp uses `2025.12`),
  `--hypothesis-seed`, `--max-examples`, and `--disable-deadline`. Run at **base** (cached at task creation) and
  on the agent tree. Compare **per-node pass sets**, not counts (dpnp CI only posts counts).
- **Gate**: `target_nodes ⊆ newly_passing` and `previously_passing − now_passing − flaky_allow = ∅`.
- **Score**: net pass delta. Skips file is protected (§3.2.2).
- Hypothesis makes this suite nondeterministic, so calibrate `flaky_allow` by 3 oracle runs.

### 5.4 `perf_ab` — performance-regression / speedup grader

- Use the repos' existing **ASV** suites (dpctl, dpnp, mkl_fft, mkl_random, mkl_umath) or a task-local
  `pytest-benchmark` file (xgboost, sklearnex).
- Shared runners are noisy, so **never compare against a stored absolute baseline**. Build base and agent in
  the same container and run **interleaved A/B rounds** (ABAB…, ≥ 10 rounds) with pinned threads and CPU
  affinity, warm-up excluded. Compare **min or median** with a bootstrap 95% CI. This is the logic of
  `asv continuous --factor F base agent`. pytest-benchmark's equivalent is `--benchmark-compare-fail=min:10%`.
- Calibrate a per-task **noise floor** with A/A runs (base vs base) during self-check. Only flag regressions
  > max(10%, 3× noise).
- Add a **noise-free proxy** for CPU code where meaningful: instruction counts (`perf stat -e instructions` or
  cachegrind). Not usable for SYCL GPU or for memory-bound kernels, where time is the metric.
- **Gate use**: "no regression > threshold on the listed benchmarks" for fix and feature tasks touching hot
  paths. **Score use**: speedup for perf tasks (xgboost#11808 adaptive block size, #11519 array tree layout)
  always **conditioned on** the correctness gate (5.2 prediction equivalence).
- Feasibility: CPU perf works on hosted runners but is noisy, so self-hosted or bare-metal is preferred for
  scoring. GPU perf needs Intel GPU runners.

### 5.5 `patching` — API-compat / patching grader

- **sklearnex**:
  - (1) The accelerated path is actually taken: capture the `sklearnex` logger at INFO and require
    `running accelerated version on CPU` for supported configs and `fallback to original Scikit-learn` for
    unsupported ones (sklearnex#3357 NaN fallback).
  - (2) Patched and unpatched results agree (5.2).
  - (3) Run `sklearnex/tests/test_patching.py::test_standard_estimator_signatures`,
    `test_docstring_patching_match` and `test_patch_map_match` as regression.
  - (4) The targeted **sklearn upstream tests** pass via `run_sklearn_tests` with the task's removed
    deselections.
- **mkl_fft / mkl_random / mkl_umath**: `patch_numpy_*()` → check `numpy.fft.fft is mkl_fft…` (or ufunc loops
  replaced). `restore_*()` → identity restored and `is_patched()` False. Do patch/restore twice and under
  threads (reversibility is an explicit guardrail in mkl_fft's copilot-instructions).
- **Signature parity**: `inspect.signature(dpnp.f)` vs `numpy.f` (parameter names, defaults, keyword-only
  markers), and return **types** (mkl_random#167: Python `float` vs 0-d array).
- **dpnp compute-follows-data**: result `sycl_queue`/`usm_type` must follow inputs (a dpnp#3045 review point).
  Assert `res.sycl_queue == x.sycl_queue` and `res.usm_type` per dpnp's coercion rules.

### 5.6 `coverage_matrix` — dtype/device coverage grader (for test-writing tasks)

- Collect the agent's tests with `pytest --collect-only -q`, then run them under an **instrumentation shim**
  that wraps the target API (monkeypatch) and records `(dtype, shape-class, layout, device, usm_type)` of every
  call.
- **Gate**: required cells covered, e.g. dpnp `get_all_dtypes(no_none=True)` × `{C, F, strided}` ×
  `{0-d, empty, n-d}`; sklearnex `get_dataframes_and_queues("numpy,pandas,dpnp", "cpu")` × `{float32, float64}`.
- Combined with mutation kill rate (5.8), this rewards tests that both **cover** the matrix and **detect**
  faults.

### 5.7 `leak` — resource-leak grader

- Several 2026 fixes are leaks with no test: dpctl#2294 (Cython `__dealloc`), mkl_umath#263 (module ref on
  init failure), mkl_fft#373 (DFTI capsule), sklearnex#3361/#3403 (refcount/buffer ownership), dpnp#3042
  (`sycl::queue` leak).
- The grader runs a task-provided repro loop N times and checks:
  - `tracemalloc` / RSS slope ≈ 0 (threshold calibrated on the oracle);
  - `sys.getrefcount` deltas for Python objects;
  - for C/C++, an ASan/LSan build (`-fsanitize=address`, `PYTHONMALLOC=malloc`, `ASAN_OPTIONS=detect_leaks=1`)
    where the toolchain allows (clang builds of mkl_*; not icpx SYCL device code).

### 5.8 `mutation` — mutation kill rate (Python + C++)

- **Primary method**: hand-curated mutant patches, as oneDAL does (`mutants/*.patch`, each proven killed by
  the oracle tests, `min_kill` ≈ 0.6). These are the only reliable option for `.pyx`, `.c.src` templates and
  SYCL kernels.
- **Candidate generation** (offline, at task-authoring time): **mutmut 3** or **cosmic-ray** for pure-Python
  modules (dpnp `dpnp_iface_*.py`, sklearnex dispatch code). For C/C++, **Mull** (a clang/LLVM plugin tied to a
  specific LLVM version; docs show 0.26.1 against LLVM 18, and newer releases exist) works for clang-built
  mkl_* C or xgboost C++. It is **not practical for icpx SYCL** (version/ABI coupling, device code). Dextool
  mutate (libclang source-level) is a further option for plain C/C++. Curate the survivors into patches and
  discard **numerically equivalent mutants**: e.g. reordering a sum changes results only within tolerance, so no
  correct test can kill it. The oracle-must-kill rule filters these automatically.
- **Science-flavored mutation operators** worth hand-writing: off-by-one in rejection-sampling masks
  (mkl_random#172), `>`→`>=` on NaN paths (mkl_umath#269), dropped `norm` factor (mkl_fft#370), wrong axis set
  in N-D scaling, `float64` hardcode (sklearnex#3295), ignored USM offset (dpnp#3068), missing event dependency
  (dpnp#3072), wrong stride sign, skipped last feature (oneDAL's `m7_skip_last_feature`).

### 5.9 `docstring` — doc/docstring task grader

- `numpydoc` validation (`python -m numpydoc lint` / `numpydoc.validate`) on the changed public symbols.
  Run `pytest --doctest-modules` on touched modules (examples must execute on the CPU device).
- Run a **single-page `sphinx-build -W`** (dpnp, dpctl, sklearnex have Sphinx docs). Check cross-reference roles
  resolve (dpnp#3065 fixed malformed `:obj:` roles, #2966 added missing `Default:`/`optional` markers), so a
  regex+Sphinx check per repo convention is fully deterministic.
- For sklearnex, `test_docstring_patching_match`. For mkl_umath, regeneration determinism of
  `ufunc_docstrings_numpy{1,2}.py` via `generate_umath_doc.py`.

### 5.10 `review_replay+probe` — executable gold comments

- Extend oneDAL's `review_replay` (gold comments matched by file/line window, recall score). When a gold
  comment carries a **reproducer** (dpnp#3045 has many: "scalar key silently takes the first value instead of
  raising ValueError", "missing `idx.ndim > 1` check"), store it as `probes/<id>.py`, which must fail on the
  reviewed commit.
- Add an optional **review→fix** stage: after the review, the agent fixes its own findings, and probes become
  hidden tests. This turns subjective review comments into deterministic tests and rewards findings that are
  real.

### 5.11 `guidance_rules` — static rule checks for the arms comparison

- mkl_fft/mkl_umath copilot-instructions state testable rules: "edit `*.c.src`, not generated C"; "bug fixes
  include a regression test"; "CHANGELOG entry" (reviewers on mkl_random#172 and dpnp#3045 flag CHANGELOG
  issues); "keep reversible patching".
- Grade them as diff predicates (changed generated file ⇒ violation; fix without a new or changed test ⇒
  violation; missing CHANGELOG line with PR link ⇒ minor). Report per arm (`guidance:none` vs `raw`) to measure
  whether the guidance changes behavior.

### 5.12 `threads` — free-threading / concurrency (optional new family)

- Run the target under `pytest-run-parallel` or a thread-pool stress harness on a **cp314t** interpreter.
  Check results match the serial run and there are no crashes or leaks. The repos all ship
  `test_freethreading.py` patterns (mkl_random, mkl_umath, sklearnex `test_free_threading.py`).

---

## 6. (c) Example task specs (tailored)

Format follows oneDAL `task.json`, with v2 keys (`tests`, `requires_devices`, `numerics`, …). Prompts are
shortened here, and every prompt ends with the oneDAL-style constraints ("no GPU; do not weaken or delete existing
tests; do not edit skip/deselect lists").

### 6.1 Build family

1. **`build_dpctl_cpu`**: build dpctl from source and run the queue tests on the OpenCL CPU device.
   ```json
   {"id":"build_dpctl_cpu","family":"build","repo":"dpctl","base":"<sha>","grader":"cmd",
    "expect":{"cmd_regex":"(build_locally\\.py|pip install .*-e)","smoke":"python -m dpctl -f",
              "tests":{"pyargs":"dpctl.tests.test_sycl_queue"}},
    "requires_devices":["cpu"],
    "prompt":"Build dpctl from this checkout (icpx is on PATH via conda) and run dpctl/tests/test_sycl_queue.py on the OpenCL CPU device. Do not modify files. End with ```json {\"command\": \"...\", \"selector\": \"<ONEAPI_DEVICE_SELECTOR value>\"}```."}
   ```
2. **`build_mkl_umath_meson022`**: env pinned to meson-python ≥0.22 at the mkl_umath#275 base. The package
   builds but `import mkl_umath` fails (`libmkl_umath_loops.so` not found). Grader: import plus
   `pytest mkl_umath/tests/test_basic.py`. Hidden oracle is the #275 diff (+9 lines in meson.build).
3. **`build_sklearnex_nodpc`**: build sklearnex against `dal-devel` with `NO_DPC=1 NO_DIST=1` and run
   `pytest sklearnex/cluster/tests`. Expect `DALROOT` to be discovered. Grades the env-var and setup.py
   knowledge in `AGENTS.md`, making it a good arms task.
4. **`build_xgboost_sycl_plugin`**: configure `-DPLUGIN_SYCL=ON -DGOOGLE_TEST=ON` and run
   `testxgboost --gtest_filter=*Sycl*Predictor*` on a CPU SYCL device.

### 6.2 Fix with hidden tests (family `fix`, grader `test_pass`, gate `strict_pass`)

1. **`fix_dpnp_median_kept_dims`** (dpnp#3081, Python-only → overlay mode)
   ```json
   {"id":"fix_dpnp_median_kept_dims","family":"fix","repo":"dpnp","base":"<merge>^","grader":"test_pass","gate":["strict_pass"],
    "source_pr":3081,"hidden_files":["dpnp/tests/test_statistics.py","dpnp/tests/test_nanfunctions.py"],
    "tests":{"pyargs":"dpnp.tests","hidden_k":"median and (empty or keepdims)"},
    "regression":{"pyargs":"dpnp.tests.test_statistics"},
    "secondary":[{"grader":"differential","apis":["median","nanmedian"],"vs":"numpy","corpus":"numerics/shapes_empty.npz"}],
    "build":{"python_overlay":true},"requires_devices":["cpu"],
    "prompt":"dpnp.median and dpnp.nanmedian raise ValueError when axis is a tuple and a kept (non-reduced) dimension has size 0, and return the wrong shape for size-1 kept dims with keepdims=True. Make them match NumPy. ..."}
   ```
2. **`fix_dpnp_cumsum_rows`** (dpnp#3063, SYCL kernel, rebuild): prompt states the symptom "cumsum along axis 1
   of a (n, m) array silently returns wrong rows for large m". Grader `test_pass` (hidden
   `test_tensor_accumulation.py` node) plus `differential` (cumsum/cumprod/nancumsum vs numpy over a size sweep
   crossing the multi-level scan threshold).
3. **`fix_mkl_random_randint_high`** (mkl_random#172): hidden `test_random.py` nodes, plus a **distributional
   gate** with `seed=1234`, `size=2**20` per dtype: `max(sample) < high` and chi-square uniformity p > 1e-3.
4. **`fix_sklearnex_forest_nan_fallback`** (sklearnex#3357): hidden `ensemble/tests/test_forest.py` nodes plus
   a `patching` gate (NaN input → `fallback to original Scikit-learn`, finite input → `running accelerated
   version on CPU`, predictions equal to stock sklearn on NaN data).
5. **`fix_sklearnex_array_api_deselect`** (sklearnex#3348, C++ rebuild): hidden tests are **the 17 sklearn
   node IDs removed from `deselected_tests.yaml`** (`deselected_tests.yaml` is protected and restored before
   grading).

### 6.3 Feature (family `feature`)

1. **`feat_mkl_fft_norm_axes`** (mkl_fft#370, re-framed as a feature): "make the root N-D API resolve `norm`
   from the transformed axes like `interfaces.*`". Graders: hidden `test_dispatch_equivalence.py` plus
   `numerics` (ULP vs `scipy.fft` at float64, ≤ 4 ULP **(bound to calibrate)**).
2. **`feat_xgboost_adaptive_block`** (xgboost#11808): gate is all `tests/cpp/common/test_hist_util*` and
   `tests/python/test_updaters.py::*hist*` passing, plus **bitwise-identical model dump** vs base on 3 fixed
   datasets (block size must not change the math). Score: `perf_ab` speedup on a synthetic wide dense dataset,
   noise-calibrated.
3. **`feat_dpnp_api_<new func>`**: add a NumPy function missing from dpnp (pick from the NumPy-suite skip table
   `skipped_tests_numpy.tbl` entries for an unimplemented API). Graders: `test_pass` on the removed `.tbl`
   lines (NumPy's own tests), `patching`-style signature parity with `numpy.<func>`, `differential` vs NumPy,
   `docstring` (numpydoc + "Default:" convention).
4. **`feat_sklearnex_ridge_solver_attr`** (sklearnex#3432): set `solver_` and match sklearn semantics. Graders:
   sklearn upstream nodes, `patching` attribute parity, accelerated-path log.

### 6.4 PR-review replay (family `review`, grader `review_replay`, score recall)

1. **`review_replay_dpnp3045`**: flat indexing. 39 inline comments, many with NumPy reproducers. Gold = the
   ~14 root comments classified `{correctness, perf, compute-follows-data, test-hygiene}`. Add `probes/` for
   the ≥6 correctness comments (§5.10).
2. **`review_replay_sklearnex3182`**: SVM namespace checks / model re-creation, 43 inline comments. Large-diff
   tier.
3. **`review_replay_mkl_random172`**: 19 comments, including the "near-twin function still has the old bug"
   finding (tests cross-function reasoning) and the CHANGELOG-structure nit (tests guidance adherence).
4. **`review_replay_xgboost11519`** (Intel CPU inference, 27 comments) or **`…11808`** (27 comments): C++
   performance-code review in a non-Intel-owned repo with different maintainer conventions.
5. **`review_replay_dpnp2914`**: small kernel diff, 16 comments. The key gold comment is "don't compare to
   NumPy here; NumPy builds disagree" (tests numerical-oracle judgment).

### 6.5 Seeded-bug review with decoys and clean controls (family `review`, grader `review`)

1. **`review_seeded_cython_lifetimes`** (dpctl) seeds:
   - S1 `__dealloc` typo (from #2294);
   - S2 missing `Py_DECREF` on an error path (from mkl_umath#263 pattern);
   - S3 `nogil` block touching a Python object;
   - S4 returning a 0-d array where NumPy returns a scalar (mkl_random#167).

   Decoys: correct `__dealloc__` with an unusual comment, `with nogil:` around pure C calls, `cdef inline`
   helpers.
2. **`review_seeded_numerics_c`** (mkl_umath) seeds:
   - S1 `x > 0 ? x : -x` for abs (NaN sign);
   - S2 `float` accumulator for a `double` loop;
   - S3 missing `npy_clear_floatstatus` / FP-exception handling;
   - S4 integer overflow in a size computation `int n = len * itemsize`.

   Decoys: `fabs` on purpose, intentional `MKL_VML_EP` low-accuracy mode documented in a comment.
3. **`review_seeded_sycl_kernel`** (dpnp.tensor) seeds:
   - S1 missing event dependency (`depends` not forwarded, dpnp#3072);
   - S2 ignored USM offset (#3068);
   - S3 work-group size not checked against `max_work_group_size`;
   - S4 `sycl::atomic_ref` on a 64-bit type without an `atomic64` aspect check;
   - S5 bool treated as 0/1 byte (#3055).

   Decoys: local-memory barriers that are correct, `sycl::ext::oneapi` usage guarded by a feature macro.
4. **`review_seeded_sklearnex_dispatch`** seeds:
   - S1 `_onedal_supported` condition missing the `sample_weight` check (silent wrong results instead of
     fallback);
   - S2 dtype hardcoded to `np.float64` (#3295);
   - S3 `n_jobs` not restored (#3200);
   - S4 namespace from `X` not used for output (everything-follows-X).

   Decoy: an intentional fallback with a logged reason.
5. **Clean controls**, one per repo: a merged, single-commit, maintainer-approved PR with only nit-level
   threads, e.g. dpnp#3051 (Numpy → NumPy capitalization), mkl_fft#365 (Coverity silence), xgboost#12618.
   Score = number of blocker/major findings (lower is better), as in oneDAL `review_clean_control`.

### 6.6 Test-writing graded by mutation kill rate (family `test`, grader `mutation`)

1. **`test_xgboost_sycl_mean`** (from xgboost#12041, which added no test): "add gtests for the SYCL `Mean`
   used by the multiclass objective". Mutants in `plugin/sycl/common/stats.cc`: divide by `n-1`, skip last
   element, accumulate in `float`, ignore weights, wrong reduction identity. `min_kill: 0.6`, `requires_devices:
   [cpu]` (SYCL CPU device).
2. **`test_mkl_umath_special_values`**: "write tests that pin special-value behavior of `mkl_umath` ufuncs
   (abs, sign, sqrt, log, exp, arctan2)". Mutants: NaN-sign flip (from #269), `-0.0` → `+0.0` in `sign`,
   `log(0)` returns NaN not −inf, `sqrt(-0.0)` sign, `exp` overflow returns max-finite. Plus `coverage_matrix`
   gate over `{f, d, F, D}`.
3. **`test_dpnp_insert`** (Python-only module `dpnp_iface_manipulation.py`, around #3041): mutants
   auto-generated by **mutmut** then curated (bounds check removed, `indices < 0` → `<= 0`, `+= n` → `+= n-1`,
   axis None path). `coverage_matrix`: dtypes × {scalar obj, array obj, slice obj} × {negative, OOB} indices.
4. **`test_sklearnex_logreg_dtype`**: "add tests for LogisticRegression dtype handling". Mutants: coef cast to
   float64 (#3295), `intercept_` dtype mismatch, `predict_proba` dtype float64 on float32 input. Tests must use
   `get_dataframes_and_queues(device_filter_="cpu")`.
5. **`test_mkl_random_randint_bounds`**: mutants from #172 (`rng++` before mask), #177 (no `brng` range check),
   and #156 (constant overflow). The kill check runs with fixed seeds so kills are deterministic.

---

## 7. (d) Feasibility: free GitHub-hosted runners vs self-hosted Intel GPU

### 7.1 What works on free hosted runners (ubuntu-latest / windows-latest, x86-64, no GPU)

| capability | how | evidence |
|---|---|---|
| SYCL **CPU device** | Intel **OpenCL CPU runtime** `intel-opencl-rt` (Intel conda channel, conda-forge, or PyPI wheel). Linux: ICD via `etc/OpenCL/vendors/intel-cpu.icd` or `OCL_ICD_FILENAMES=libintelocl.so`. Windows: registry `HKLM\SOFTWARE\Khronos\OpenCL\Vendors` | dpctl `conda-package-cf.yml`, dpnp `conda-package.yml:536`, `conda-build-test.yml`; PyPI wheel contents verified by remote listing |
| `ONEAPI_DEVICE_SELECTOR=opencl:cpu` | standard DPC++ runtime env var | numba-dpex CI; dpnp benchmarks README |
| Open-source SYCL toolchain alternative | intel/llvm nightly + `oclcpuexp` (experimental OpenCL CPU RT) | dpctl/dpnp `os-llvm-sycl-build.yml` |
| Full dpctl / dpnp test suites (CPU subset) | conda env + `intel-opencl-rt` + `SYCL_CACHE_PERSISTENT=1` | both repos' main CI run on hosted runners |
| mkl_fft / mkl_random / mkl_umath | pure CPU oneMKL; pip `mkl-devel` or conda | all their CI is hosted |
| sklearnex CPU (+daal4py, onedal CPU) | `NO_DPC=1 NO_DIST=1`, `dal-devel` or oneDAL nightly artifact | `ci.yml` on `ubuntu-26.04` / `windows-2025` |
| sklearnex SPMD on CPU | Intel MPI or MPICH + `mpirun -n 2..4` (small) | `tests/AGENTS.md`; CI disables it with `NO_DIST=1`, so it is feasible but slow |
| xgboost CPU and SYCL-on-CPU | conda env `linux_sycl_test.yml` (dpcpp, onedpl) | `sycl_tests.yml` on `ubuntu-latest` |
| array-api-tests vs dpnp | pinned commit + seed | dpnp `array-api-conformity` job (15 min budget) |

**Not available on hosted runners**:
- **Level Zero** has no CPU device (L0 is the GPU driver interface), so every `level_zero:*` path is GPU-only.
  The DPC++ **CUDA/HIP** backends (dpnp `--target-cuda/--target-hip`, `skipped_tests_cuda.tbl`) cannot run on
  Intel CPU either. GitHub's paid GPU runners are NVIDIA (not Intel), so they could only serve dpnp's CUDA
  backend.
- **fp64-less device** paths (`skipped_tests_gpu_no_fp64.tbl`, xgboost#11524/#10702) cannot be emulated: the CPU
  device reports fp64.
- **Multi-GPU** (`multi_gpu` marker), USM shared/IPC behaviors that differ on GPU (`test_sycl_ipc_memory.py`),
  GPU-specific compiler bugs (dpctl CHANGELOG: Intel Graphics Compiler workaround for `cumulative_logsumexp`).
- Stable **performance** numbers. Hosted runners are noisy, so they suit gates with A/B + noise floor (§5.4),
  not fine-grained speedup scoring.

### 7.2 Costs and limits on hosted runners

- **Image size**: dpcpp compiler + oneMKL + TBB + OpenCL RT run to several GB. Prebuild per-repo eval images
  (oneDAL already uses a Dockerfile and mounted caches), and prefer `NO_DPC=1` / python-overlay to avoid the
  compiler entirely for Python-only tasks.
- **Build time**: dpnp full SYCL build is the long pole. Use the cached base build + ccache + `rebuild_globs`.
  Estimate 30–60+ min cold vs minutes incremental **(unverified; measure in self-check)**. dpctl is
  moderate; mkl_* are fast (C/Cython only); sklearnex with `NO_DPC=1` is moderate; xgboost C++ is moderate to
  long (gtest + SYCL plugin).
- **OpenCL CPU JIT** time: the first run compiles each kernel (SPIR-V → x86). Mitigate with a pre-warmed,
  read-only `SYCL_CACHE_DIR` copied fresh per grade. Expect slower suites than GPU CI.
- **Timeouts**: hosted job limit is 6 h. dpnp CI uses 100–120 min test budgets, so per-task grading should
  target hidden + targeted regression subsets (not full suites), as oneDAL's `regression_targets` does.

### 7.3 What needs self-hosted Intel GPU runners

- Tasks tagged `requires_devices: [gpu]` cover Level Zero / OpenCL GPU correctness, dpnp GPU skip tables,
  sklearnex `--gpu` deselections and GPU estimators (DBSCAN, KMeans, PCA, KNN full GPU per AGENTS.md), xgboost
  `sycl:gpu`, and dpctl L0 IPC/kernel-bundle features.
- `[gpu_no_fp64]` (Arc A-series / iGPU) covers fp32-only devices: xgboost#11524-style numerical-reordering
  bugs, dpnp no-fp64 dtype downgrades.
- `[multi_gpu]` covers dpnp `multi_gpu` marker tests and sklearnex SPMD GPU.
- Performance scoring for SYCL kernels.
- Options: self-hosted runners on Intel Arc/Flex/Max hardware or a cloud Intel GPU host **(availability and cost
  not researched)**. The framework should run the CPU pool everywhere and dispatch GPU-tagged tasks only to
  runners whose device probe (§4 A4) satisfies the requirement, marking them `infeasible` elsewhere rather than
  failing.

### 7.4 Recommended rollout

1. **Pilot pool (CPU, hosted, cheap)**: mkl_umath, mkl_fft, mkl_random (fast meson builds, existing AGENTS.md for
   the arms comparison), plus dpnp **Python-overlay** fix tasks (#3081, #3041) and sklearnex `NO_DPC=1` tasks
   (#3357, #3295).
2. **Phase 2 (CPU, heavier builds)**: dpnp/dpctl kernel fixes (#3043, #3063, #2382), xgboost SYCL-on-CPU and
   CPU perf (#12041 test-writing, #11808/#11519 perf + review replay), the array-API delta grader.
3. **Phase 3 (self-hosted Intel GPU)**: GPU / no-fp64 / multi-GPU tasks, SYCL perf scoring.
4. **Every phase**: `run.py check` (untouched fails, oracle passes, oracle×3 stable) in CI, which is
   model-free and token-free like oneDAL's `agent-evals.yml`.

---

## 8. Leakage and determinism checklist (Python-specific additions to oneDAL's rules)

- Network off. The env must not contain the package under test at any version, and no newer dependency release
  may embed the fix (dpnp ↔ dpctl coupling: pin dpctl to the base's required version).
- Delete `.git` history (oneDAL does `git archive`). `CHANGELOG.md` at a `<merge>^` base already predates the
  fix, but in **review-replay** tasks the reviewed commit's own CHANGELOG line and PR links stay visible. That is
  fine for review, but strip them from any derived **review→fix** stage.
- Hidden tests are written into the **imported** tests package (pyargs) and removed after grading. Regrading is
  idempotent.
- Protected files (skip tables, `deselected_tests.yaml`, `array-api-skips.txt`, `conftest.py` collection hooks,
  `pyproject.toml [tool.pytest]`) are restored from base before grading. Changes to them are reported.
- Reject agent edits that change tolerances in the repo's own helpers (`assert_dtype_allclose` `factor`,
  mkl_fft per-test `rtol`): restore `dpnp/tests/helper.py` from base.
- Determinism knobs: `PYTHONHASHSEED=0`, `--hypothesis-seed`, `-p no:randomly`, pinned thread counts,
  `MKL_CBWR` for bitwise MKL, fixed `random_state` everywhere, and a frozen `flaky_allow` from oracle×3.

---

## 9. Sources

Repos (shallow clones, HEAD 2026-10-13) under `(local shallow clones)/`: dpctl, dpnp, mkl_fft,
mkl_random, mkl_umath, numba-dpex, scikit-learn-intelex, xgboost. oneDAL framework:
`oneDAL@pr-3818/dev/agent_evals/`.

PRs (all `gh pr view` / `gh api …/pulls/N/comments`):
- dpctl: #2382, #2294, #2219, #2229
- dpnp: #3043, #3081, #3063, #2914, #3045, #3055, #3041, #3068, #3072, #3051, #3065, #2966
- mkl_fft: #370, #373, #358, #365
- mkl_random: #172, #167, #177, #156
- mkl_umath: #269, #263, #275
- scikit-learn-intelex: #3357, #3295, #3348, #3182, #3198, #3467, #3432, #3361, #3403
- xgboost: #12041, #11808, #11519, #12604, #12541, #12618, #11524

Web:
- [intel-opencl-rt on PyPI](https://pypi.org/project/intel-opencl-rt/)
- [Intel CPU Runtime for OpenCL Applications Guide](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-cpu-runtime-for-opencl-applications-guide.html) (`libintelocl.so`, `OCL_ICD_FILENAMES` / `.icd` registration)
- [Intel Community: OpenCL in GitHub Actions on Windows](https://community.intel.com/t5/Mobile-and-Desktop-Processors/Using-OpenCL-in-GitHub-Actions-running-on-windows/td-p/1498517) (no GPUs on Actions; CPU OpenCL)
- [intel/llvm#7659](https://github.com/intel/llvm/issues/7659) (`OCL_ICD_FILENAMES=libintelocl.so` with dpctl)
- [Mull](https://github.com/mull-project/mull), [Mull docs (LLVM coupling, incremental/diff mode)](https://mull.readthedocs.io/en/latest/Features.html)
- [pytest-benchmark docs](https://pytest-benchmark.readthedocs.io/), [pytest-test-categories perf guide (`--benchmark-compare-fail=mean:10%`)](https://pytest-test-categories.readthedocs.io/en/latest/performance.html)
- [pykeen ASV CI (`asv continuous --factor 1.2`)](https://github.com/pykeen/pykeen/pull/1674)
- PyPI JSON API for `intel-opencl-rt`, `dpcpp-cpp-rt`, `intel-sycl-rt`, `dpnp` (0.20.0), `dpctl` (0.22.1), `mkl-fft` (2.3.2), `mkl-umath` (0.5.0)
