# Starter configurations

Ready-to-adapt `repoevals.toml` files for scientific Python and C++ libraries. Copy the file for your project to the
repository root as `repoevals.toml`, adjust the build command to your environment, and add tasks under
`.agent-evals/tasks/`.

| file | project | test adapter | notes |
|---|---|---|---|
| [dpnp.repoevals.toml](dpnp.repoevals.toml) | IntelPython/dpnp | pytest | SYCL CPU device via the OpenCL CPU runtime; skip tables are protected |
| [dpctl.repoevals.toml](dpctl.repoevals.toml) | IntelPython/dpctl | pytest | SYCL CPU device via the OpenCL CPU runtime |
| [mkl_fft.repoevals.toml](mkl_fft.repoevals.toml) | IntelPython/mkl_fft | pytest | meson-python build with `mkl-devel` |
| [mkl_random.repoevals.toml](mkl_random.repoevals.toml) | IntelPython/mkl_random | pytest | meson-python build with `mkl-devel` |
| [mkl_umath.repoevals.toml](mkl_umath.repoevals.toml) | IntelPython/mkl_umath | pytest | meson-python build with `mkl-devel` |
| [scikit-learn-intelex.repoevals.toml](scikit-learn-intelex.repoevals.toml) | uxlfoundation/scikit-learn-intelex | pytest | CPU-only build (`NO_DPC=1`); `deselected_tests.yaml` is protected |
| [xgboost.repoevals.toml](xgboost.repoevals.toml) | dmlc/xgboost | ctest | C++ unit tests through CTest |
| [onedal.repoevals.toml](onedal.repoevals.toml) | uxlfoundation/oneDAL | bazel | Bazel test targets |

## Hardware-specific tasks

Tasks that need a GPU declare it, and are reported as `skipped` on hosts without one:

```toml
[environment]
requires = { devices = ["gpu"] }
```

Set `REPOEVALS_DEVICES=cpu,gpu` on hosts that have a GPU.

## Tips for numerical libraries

- Protect skip and deselect lists with `[policy].protected_globs` so they cannot be edited to hide failures.
- Pin thread counts (`OMP_NUM_THREADS`, `MKL_NUM_THREADS`) in `[test].env` for reproducible results.
- In `numeric` tasks, compare against a pinned reference build (for example a specific NumPy version), and cover
  every supported dtype, empty and non-contiguous inputs, and NaN/Inf values.
