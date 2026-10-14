# Starter configurations

Ready-to-adapt `repoevals.toml` files for scientific Python and C++ libraries. Start from one with
`repoevals init --preset <name>` (or copy the file to your repository root as `repoevals.toml`), adjust the build
commands to your environment, run `repoevals doctor`, and add tasks under `.agent-evals/tasks/`.

Each configuration builds the project once per workspace (`[environment].setup`) before the agent starts, and
rebuilds while grading only when compiled sources change (`[test].rebuild_globs`).

| preset | project | test adapter | notes |
|---|---|---|---|
| [`dpnp`](dpnp.repoevals.toml) | IntelPython/dpnp | pytest | SYCL CPU device via the OpenCL CPU runtime; skip tables are protected |
| [`dpctl`](dpctl.repoevals.toml) | IntelPython/dpctl | pytest | SYCL CPU device via the OpenCL CPU runtime |
| [`mkl_fft`](mkl_fft.repoevals.toml) | IntelPython/mkl_fft | pytest | meson-python build with `mkl-devel` |
| [`mkl_random`](mkl_random.repoevals.toml) | IntelPython/mkl_random | pytest | meson-python build with `mkl-devel` |
| [`mkl_umath`](mkl_umath.repoevals.toml) | IntelPython/mkl_umath | pytest | meson-python build with `mkl-devel` |
| [`scikit-learn-intelex`](scikit-learn-intelex.repoevals.toml) | uxlfoundation/scikit-learn-intelex | pytest | CPU-only build (`NO_DPC=1`); `deselected_tests.yaml` is protected |
| [`xgboost`](xgboost.repoevals.toml) | dmlc/xgboost | ctest | C++ unit tests through CTest; initialize git submodules in your checkout first |
| [`onedal`](onedal.repoevals.toml) | uxlfoundation/oneDAL | bazel | Bazel test targets |

## Hardware-specific tasks

Tasks that need a GPU declare it, and are reported as `skipped` on hosts without one:

```toml
[environment]
requires = { devices = ["gpu"] }
```

Set `REPOEVALS_DEVICES=cpu,gpu` on hosts that have a GPU.

## SYCL CPU device from PyPI

Projects using SYCL (dpnp, dpctl) run on CPU-only machines with Intel's OpenCL CPU runtime from PyPI
(`pip install intel-opencl-rt`). Its bundled `intel-cpu.icd` points at a placeholder path, so name the driver
directly:

```bash
export OCL_ICD_FILENAMES=$(python -c "import sys; print(sys.prefix)")/lib/libintelocl.so
export ONEAPI_DEVICE_SELECTOR=opencl:cpu
```

To evaluate without a SYCL compiler, install a released wheel and author tasks at that release
(`repoevals author mine --release-base <tag>`, `author fix --base <tag> --extract-tests`); copy the wheel's compiled
modules into each workspace with `[environment].setup`.

## Tips for numerical libraries

- Protect skip and deselect lists with `[policy].protected_globs` so they cannot be edited to hide failures.
- Pin thread counts (`OMP_NUM_THREADS`, `MKL_NUM_THREADS`) in `[test].env` for reproducible results.
- In `numeric` tasks, compare against a pinned reference build (for example a specific NumPy version), and cover
  every supported dtype, empty and non-contiguous inputs, and NaN/Inf values.
