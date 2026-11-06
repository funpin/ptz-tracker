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


def test_gear_speeds_are_even_steps_up_to_full():
    from ptztracker.control.manual_curve import PAD_GEAR_DEG_S
    assert len(PAD_GEAR_DEG_S) == 8 and PAD_GEAR_DEG_S[-1] == 90
    ratios = [b / a for a, b in zip(PAD_GEAR_DEG_S, PAD_GEAR_DEG_S[1:])]
    assert all(1.4 < r < 1.9 for r in ratios)  # каждая ступень примерно в 1,6 раза быстрее


def test_pad_velocity_reaches_gear_speed_at_full_tilt():
    from ptztracker.camera.he42 import pan_base_deg_s
    from ptztracker.camera.speed import velocity_to_code
    from ptztracker.control.manual_curve import PAD_GEAR_DEG_S, pad_velocity
    for g in PAD_GEAR_DEG_S:
        deg = pan_base_deg_s(velocity_to_code(pad_velocity(1.0, g)))
        assert deg == pytest.approx(g, rel=0.15)  # код камеры целый: точнее не выйдет
        assert pad_velocity(-1.0, g) == -pad_velocity(1.0, g)
    assert pad_velocity(0.0, 21) == 0.0


def test_pad_velocity_never_stops_a_moving_stick_and_grows():
    from ptztracker.camera.speed import velocity_to_code
    from ptztracker.control.manual_curve import MIN_OUT, pad_velocity
    assert velocity_to_code(pad_velocity(MIN_OUT, 3)) > 50  # самая малая скорость стика двигает камеру
    vs = [pad_velocity(i / 50, 90) for i in range(51)]
    assert vs == sorted(vs)


def test_no_burst_at_the_edge_of_the_stick():
    """Жалоба 2026-11-06: у упора камера резко прибавляет. Последняя пятая часть хода — меньше половины скорости."""
    from ptztracker.camera.he42 import pan_base_deg_s
    from ptztracker.camera.speed import velocity_to_code
    from ptztracker.control.manual_curve import pad_velocity

    def deg(a):
        return pan_base_deg_s(velocity_to_code(pad_velocity(shape(a, DEFAULT_SENS), 90)))
    assert deg(1.0) - deg(0.8) < 0.5 * deg(1.0)
