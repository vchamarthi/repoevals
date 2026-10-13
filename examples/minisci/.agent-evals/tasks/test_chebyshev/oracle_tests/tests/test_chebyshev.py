import pytest

import minisci


def test_basic_max_abs():
    assert minisci.chebyshev([0.0, 3.0, 1.0], [1.0, -1.0, 1.5]) == 4.0


def test_negative_difference_uses_abs():
    assert minisci.chebyshev([0.0, 0.0], [5.0, 1.0]) == 5.0


def test_max_not_sum():
    assert minisci.chebyshev([1.0, 1.0], [0.0, 0.0]) == 1.0


def test_last_element_counts():
    assert minisci.chebyshev([0.0, 0.0, 0.0], [0.0, 0.0, 7.0]) == 7.0


def test_shape_mismatch():
    with pytest.raises(ValueError):
        minisci.chebyshev([1.0, 2.0], [1.0])


def test_empty_is_zero():
    assert minisci.chebyshev([], []) == 0.0


def test_float64_precision():
    assert minisci.chebyshev([0.1], [0.0]) == 0.1
