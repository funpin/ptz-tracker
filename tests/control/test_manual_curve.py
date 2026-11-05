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
        assert all(a <= b for a, b in zip(vals, vals[1:]))
        assert vals[0] < vals[100] < vals[-1]


def test_small_tilt_still_moves_camera():
    from ptztracker.camera.speed import velocity_to_code
    for sens in (0, 50):
        assert velocity_to_code(shape(0.1, sens)) == 52  # самый медленный ход; мёртвая зона та же, что в 1.0.1
        assert velocity_to_code(shape(-0.1, sens)) == 48
    assert shape(0.01, 0) == pytest.approx(1e-6)  # что стояло — стоит


def test_out_of_range_clamped():
    assert shape(1.5, 50) == pytest.approx(1.0)
    assert shape(0.5, 300) == pytest.approx(0.5)
    assert shape(0.5, -20) == pytest.approx(0.125)


def test_tele_scale_by_field_of_view():
    from ptztracker.camera import he42
    from ptztracker.control.manual_curve import TELE_SCALE_MIN, tele_scale
    assert tele_scale(None) == 1.0  # зум не знаем — не замедляем
    assert tele_scale(1.0) == pytest.approx(1.0)
    mid = he42.ZOOM_RATIO ** 0.5
    assert tele_scale(mid) == pytest.approx(he42.hfov_deg(0.5) / he42.HFOV_WIDE_DEG)
    assert tele_scale(he42.ZOOM_RATIO) == TELE_SCALE_MIN == 0.15
    assert tele_scale(0.5) == pytest.approx(1.0)  # мусор — без замедления
    vals = [tele_scale(1.0 + i * 0.2) for i in range(96)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))


def test_tele_strength_softens_slowdown():
    from ptztracker.control.manual_curve import TELE_SCALE_MIN, tele_scale

    assert tele_scale(8.0, 100) == tele_scale(8.0)  # «Сильно» — как было
    assert tele_scale(8.0) == TELE_SCALE_MIN
    mid = tele_scale(8.0, 50)
    assert abs(mid - 0.39) < 0.01  # ≈ корень из прежнего множителя
    assert tele_scale(8.0, 0) == 1.0  # «ноль» — не замедлять
    assert tele_scale(2.0, 25) > tele_scale(2.0, 50) > tele_scale(2.0, 100)
    assert tele_scale(None, 50) == 1.0
