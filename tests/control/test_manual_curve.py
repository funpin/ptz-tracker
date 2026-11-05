import pytest

from ptztracker.control.manual_curve import DEFAULT_SENS, exponent, shape


def test_full_tilt_is_full_speed_at_any_sensitivity():
    for sens in (0, 25, 50, 75, 100):
        assert shape(1.0, sens) == pytest.approx(1.0)
        assert shape(-1.0, sens) == pytest.approx(-1.0)


def test_zero_and_sign():
    assert shape(0.0, 0) == 0.0
    assert shape(-0.5, 50) == pytest.approx(-shape(0.5, 50))
    assert shape(-0.5, 50) < 0


def test_exponent_from_linear_to_cubic():
    assert exponent(100) == pytest.approx(1.0)
    assert exponent(50) == pytest.approx(2.0)
    assert exponent(0) == pytest.approx(3.0)
    assert DEFAULT_SENS == 50


def test_half_tilt():
    assert shape(0.5, 100) == pytest.approx(0.5)  # «Резко» — как в 1.0.1
    assert shape(0.5, 50) == pytest.approx(0.25)
    assert shape(0.5, 0) == pytest.approx(0.125)


def test_monotonic():
    for sens in (0, 50, 100):
        vals = [shape(i / 100, sens) for i in range(-100, 101)]
        assert all(a < b for a, b in zip(vals, vals[1:]))


def test_out_of_range_clamped():
    assert shape(1.5, 50) == pytest.approx(1.0)
    assert shape(0.5, 300) == pytest.approx(0.5)
    assert shape(0.5, -20) == pytest.approx(0.125)
