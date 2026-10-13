import numpy as np


def chebyshev(a, b):
    """Chebyshev (L-infinity) distance between two vectors of equal length."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError("shape mismatch")
    if a.size == 0:
        return 0.0
    return float(np.abs(a - b).max())
