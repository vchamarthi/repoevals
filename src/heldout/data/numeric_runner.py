"""Executed by the numeric grader in the repository's Python environment:

    python numeric_runner.py <probe.py> <out.json> <tolerances-json>

probe.py (lives in the task directory, never in the agent's workspace) defines
    CASES: list[dict]           each case needs an "id"; everything else is passed through
    candidate(case) -> array    calls the library under test (the agent's tree)
    reference(case) -> array    trusted implementation (NumPy, SciPy, scikit-learn, mpmath, closed form)
    optional compare(case, got, want) -> dict with at least {"ok": bool}

Default comparison: same shape; NaN/Inf positions identical; |got - want| <= atol + rtol * |want| element-wise,
with rtol/atol chosen by the reference dtype (overridable per case with "rtol"/"atol"). Also reports max ULP
distance for floating dtypes.
"""
import importlib.util
import json
import sys
import traceback

DEFAULT_TOL = {"float16": (1e-3, 1e-3), "float32": (1e-5, 1e-6), "float64": (1e-12, 1e-14),
               "complex64": (1e-5, 1e-6), "complex128": (1e-12, 1e-14)}


def ulp_distance(np, got, want):
    if got.dtype.kind != "f" or got.dtype != want.dtype or got.size == 0:
        return None
    itype = {2: np.int16, 4: np.int32, 8: np.int64}[got.dtype.itemsize]
    a, b = got.view(itype).astype(np.int64), want.view(itype).astype(np.int64)
    # map sign-magnitude to a monotonic integer line
    a = np.where(a < 0, np.iinfo(itype).min - a, a)
    b = np.where(b < 0, np.iinfo(itype).min - b, b)
    finite = np.isfinite(got) & np.isfinite(want)
    return int(np.max(np.abs(a - b)[finite])) if finite.any() else 0


def default_compare(np, case, got, want, tol):
    got, want = np.asarray(got), np.asarray(want)
    res = {"shape_ok": got.shape == want.shape, "dtype": str(want.dtype), "got_dtype": str(got.dtype)}
    if not res["shape_ok"]:
        res.update(ok=False, why=f"shape {got.shape} != {want.shape}")
        return res
    if want.dtype.kind in "biu":
        res["ok"] = bool(np.array_equal(got, want))
        res["mismatches"] = int(np.count_nonzero(got != want))
        return res
    rtol, atol = DEFAULT_TOL.get(str(want.dtype), (1e-7, 0.0))
    rtol, atol = tol.get(str(want.dtype), (rtol, atol))
    rtol, atol = case.get("rtol", rtol), case.get("atol", atol)
    gnan, wnan = np.isnan(got), np.isnan(want)
    ginf, winf = np.isinf(got), np.isinf(want)
    special_ok = bool(np.array_equal(gnan, wnan) and np.array_equal(ginf & (got > 0), winf & (want > 0))
                      and np.array_equal(ginf & (got < 0), winf & (want < 0))) if want.dtype.kind == "f" else \
        bool(np.array_equal(gnan, wnan))
    fin = np.isfinite(want) & np.isfinite(got)
    err = np.abs(got[fin].astype(np.complex128 if want.dtype.kind == "c" else np.float64)
                 - want[fin].astype(np.complex128 if want.dtype.kind == "c" else np.float64))
    bound = atol + rtol * np.abs(want[fin])
    res.update(rtol=rtol, atol=atol, special_values_ok=special_ok,
               max_abs_err=float(err.max()) if err.size else 0.0,
               max_rel_err=float((err / np.maximum(np.abs(want[fin]), np.finfo(np.float64).tiny)).max())
               if err.size else 0.0,
               n_violations=int(np.count_nonzero(err > bound)),
               max_ulp=ulp_distance(np, got.astype(want.dtype) if got.dtype.kind == want.dtype.kind else got, want))
    res["ok"] = bool(special_ok and res["n_violations"] == 0)
    return res


def main():
    probe_path, out_path, tol_json = sys.argv[1], sys.argv[2], sys.argv[3]
    tol = {k: tuple(v) for k, v in json.loads(tol_json).items()}
    import numpy as np
    spec = importlib.util.spec_from_file_location("heldout_probe", probe_path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    results = []
    for case in probe.CASES:
        r = {"id": case.get("id", str(len(results)))}
        try:
            want = probe.reference(case)
            got = probe.candidate(case)
            if hasattr(got, "asnumpy"):
                got = got.asnumpy()
            elif "dpnp" in type(got).__module__ or "dpctl" in type(got).__module__:
                import dpnp
                got = dpnp.asnumpy(got)
            if hasattr(probe, "compare"):
                r.update(probe.compare(case, got, want))
            else:
                r.update(default_compare(np, case, got, want, tol))
        except Exception as e:  # the candidate raising is a failed case, not a grader error
            r.update(ok=False, error=f"{type(e).__name__}: {e}", tb=traceback.format_exc(limit=3))
        results.append(r)
    with open(out_path, "w") as fh:
        json.dump(results, fh, default=str)


if __name__ == "__main__":
    main()
