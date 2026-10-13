"""Numerical probe for minisci.logsumexp. Lives in the task directory; the agent never sees it."""
import math

import numpy as np

CASES = [
    {"id": "ordinary", "x": [0.0, 1.0, 2.0]},
    {"id": "large", "x": [1000.0, 1000.0]},
    {"id": "very_large_mixed", "x": [1e4, 0.0, -1e4]},
    {"id": "very_negative", "x": [-1000.0, -1001.0]},
    {"id": "single", "x": [3.5]},
    {"id": "with_minus_inf", "x": [-math.inf, 0.0]},
    {"id": "all_minus_inf", "x": [-math.inf, -math.inf]},
    {"id": "empty", "x": []},
]


def reference(case):
    x = np.asarray(case["x"], dtype=np.float64)
    if x.size == 0:
        return np.float64(-np.inf)
    m = np.max(x)
    if not np.isfinite(m):
        return np.float64(m)
    return np.float64(m + math.log(math.fsum(np.exp(x - m))))


def candidate(case):
    import minisci
    return np.float64(minisci.logsumexp(case["x"]))
