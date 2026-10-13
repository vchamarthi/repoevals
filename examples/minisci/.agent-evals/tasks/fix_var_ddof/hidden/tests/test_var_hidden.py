import numpy as np
import pytest

import minisci


@pytest.mark.parametrize("ddof", [0, 1, 2])
def test_var_matches_numpy(ddof):
    x = [1.0, 2.0, 3.0, 4.0, 10.0]
    assert minisci.var(x, ddof=ddof) == pytest.approx(np.var(x, ddof=ddof), rel=1e-12)


def test_var_rejects_too_large_ddof():
    with pytest.raises(ValueError):
        minisci.var([1.0, 2.0], ddof=2)
