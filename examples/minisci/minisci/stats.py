import numpy as np


def mean(x):
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        raise ValueError("mean of empty array")
    return float(x.sum() / x.size)


def var(x, ddof=0):
    """Variance with `ddof` delta degrees of freedom (NumPy semantics)."""
    x = np.asarray(x, dtype=np.float64)
    n = x.size
    if n - ddof <= 0:
        raise ValueError("ddof must be smaller than the number of elements")
    m = x.sum() / n
    return float(((x - m) ** 2).sum() / (n - ddof))


def logsumexp(x):
    """log(sum(exp(x))) computed without overflow."""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return float("-inf")
    m = x.max()
    if not np.isfinite(m):
        return float(m)
    return float(m + np.log(np.exp(x - m).sum()))
