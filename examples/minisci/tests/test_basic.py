import pytest

import minisci


def test_mean():
    assert minisci.mean([1, 2, 3]) == 2.0


def test_var_population():
    assert minisci.var([1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.25)


def test_mean_empty():
    with pytest.raises(ValueError):
        minisci.mean([])
