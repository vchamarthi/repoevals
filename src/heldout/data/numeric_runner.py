"""Executed by the numeric grader, in three separate processes so that code under test cannot influence the
comparison:

    python -I numeric_runner.py candidate <probe.py> <out.npz>   (agent's tree importable; may crash or cheat)
    python -I numeric_runner.py reference <probe.py> <out.npz>   (agent's tree NOT importable)
    python -I numeric_runner.py compare   <probe.py> <cand.npz> <ref.npz> <out.json> <tolerances-json>

Arrays cross process boundaries as .npz with allow_pickle=False, so a crafted candidate output cannot execute code
in the comparing process.

probe.py (lives in the task directory, never in the agent's workspace) defines
    CASES: list[dict]           each case needs an "id"; everything else is passed through
    candidate(case) -> array    calls the library under test — import it INSIDE this function
    reference(case) -> array    trusted implementation (NumPy, SciPy, scikit-learn, mpmath, closed form)
    optional compare(case, got, want) -> dict with at least {"ok": bool}

Default comparison: same shape and dtype (set "check_dtype": False in a case to relax to the same kind);
NaN/Inf positions identical (real and imaginary parts separately); |got - want| <= atol + rtol * |want|
element-wise, with rtol/atol chosen by the reference dtype (overridable per case with "rtol"/"atol").
Also reports max ULP distance for real floating dtypes.
"""
import importlib.util
import json
import sys
import traceback

DEFAULT_TOL = {"float16": (1e-3, 1e-3), "float32": (1e-5, 1e-6), "float64": (1e-12, 1e-14),
               "complex64": (1e-5, 1e-6), "complex128": (1e-12, 1e-14)}


def load_probe(path):
    spec = importlib.util.spec_from_file_location("heldout_probe", path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    return probe


def to_numpy(np, x):
    if hasattr(x, "asnumpy"):
        x = x.asnumpy()
    elif type(x).__module__.split(".")[0] in ("dpnp", "dpctl"):
        import dpnp
        x = dpnp.asnumpy(x)
    elif hasattr(x, "get") and type(x).__module__.split(".")[0] == "cupy":
        x = x.get()
    return np.asarray(x)


def produce(which, probe_path, out_path):
    import numpy as np
    probe = load_probe(probe_path)
    fn = getattr(probe, which)
    arrays, errors = {}, {}
    for i, case in enumerate(probe.CASES):
        try:
            a = to_numpy(np, fn(case))
            if a.dtype.kind == "O":
                raise TypeError("object arrays are not comparable")
            arrays[f"c{i}"] = a
        except Exception as e:  # a raising candidate is a failed case, not a grader error
            errors[f"c{i}"] = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
    np.savez(out_path, **arrays)
    with open(out_path + ".errors.json", "w") as fh:
        json.dump(errors, fh)


def ulp_distance(np, got, want):
    if got.dtype.kind != "f" or got.dtype != want.dtype or got.size == 0:
        return None
    itype = {2: np.int16, 4: np.int32, 8: np.int64}[got.dtype.itemsize]
    a, b = got.view(itype).astype(np.int64), want.view(itype).astype(np.int64)
    a = np.where(a < 0, np.iinfo(itype).min - a, a)  # sign-magnitude -> monotonic integer line
    b = np.where(b < 0, np.iinfo(itype).min - b, b)
    finite = np.isfinite(got) & np.isfinite(want)
    return int(np.max(np.abs(a - b)[finite])) if finite.any() else 0


def _special_ok(np, got, want):
    parts = [(got.real, want.real), (got.imag, want.imag)] if want.dtype.kind == "c" or got.dtype.kind == "c" \
        else [(got, want)]
    for g, w in parts:
        g, w = np.asarray(g, dtype=np.float64), np.asarray(w, dtype=np.float64)
        if not (np.array_equal(np.isnan(g), np.isnan(w)) and np.array_equal(np.isposinf(g), np.isposinf(w))
                and np.array_equal(np.isneginf(g), np.isneginf(w))):
            return False
    return True


def default_compare(np, case, got, want, tol):
    got, want = np.asarray(got), np.asarray(want)
    res = {"shape_ok": got.shape == want.shape, "dtype": str(want.dtype), "got_dtype": str(got.dtype)}
    if not res["shape_ok"]:
        res.update(ok=False, why=f"shape {got.shape} != {want.shape}")
        return res
    if case.get("check_dtype", True) and got.dtype != want.dtype:
        res.update(ok=False, why=f"dtype {got.dtype} != {want.dtype}")
        return res
    if got.dtype.kind == "c" and want.dtype.kind != "c":
        res.update(ok=False, why="complex result for a real reference")
        return res
    if want.dtype.kind in "biu":
        res["ok"] = bool(np.array_equal(got, want))
        res["mismatches"] = int(np.count_nonzero(got != want))
        return res
    rtol, atol = DEFAULT_TOL.get(str(want.dtype), (1e-7, 0.0))
    rtol, atol = tol.get(str(want.dtype), (rtol, atol))
    rtol, atol = case.get("rtol", rtol), case.get("atol", atol)
    special_ok = _special_ok(np, got, want)
    wide = np.complex128 if (want.dtype.kind == "c" or got.dtype.kind == "c") else np.float64
    g, w = got.astype(wide), want.astype(wide)
    fin = np.isfinite(w) & np.isfinite(g)
    err = np.abs(g[fin] - w[fin])
    bound = atol + rtol * np.abs(w[fin])
    res.update(rtol=rtol, atol=atol, special_values_ok=special_ok,
               max_abs_err=float(err.max()) if err.size else 0.0,
               max_rel_err=float((err / np.maximum(np.abs(w[fin]), np.finfo(np.float64).tiny)).max())
               if err.size else 0.0,
               n_violations=int(np.count_nonzero(err > bound)),
               max_ulp=ulp_distance(np, got, want))
    res["ok"] = bool(special_ok and res["n_violations"] == 0)
    return res


def compare(probe_path, cand_path, ref_path, out_path, tol_json):
    import numpy as np
    tol = {k: tuple(v) for k, v in json.loads(tol_json).items()}
    probe = load_probe(probe_path)
    cand = np.load(cand_path, allow_pickle=False) if cand_path != "-" else {}
    cand_err = json.load(open(cand_path + ".errors.json")) if cand_path != "-" else {}
    ref = np.load(ref_path, allow_pickle=False)
    ref_err = json.load(open(ref_path + ".errors.json"))
    if ref_err:
        raise SystemExit(f"reference failed: {ref_err}")
    results = []
    for i, case in enumerate(probe.CASES):
        key = f"c{i}"
        r = {"id": case.get("id", key)}
        if key not in cand:
            r.update(ok=False, error=cand_err.get(key, "candidate produced no result"))
        else:
            got, want = cand[key], ref[key]
            try:
                r.update(probe.compare(case, got, want) if hasattr(probe, "compare")
                         else default_compare(np, case, got, want, tol))
            except Exception as e:
                r.update(ok=False, error=f"compare: {type(e).__name__}: {e}")
        results.append(r)
    with open(out_path, "w") as fh:
        json.dump(results, fh, default=str)


def main():
    mode = sys.argv[1]
    if mode in ("candidate", "reference"):
        produce(mode, sys.argv[2], sys.argv[3])
    elif mode == "compare":
        compare(*sys.argv[2:7])
    else:
        raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
