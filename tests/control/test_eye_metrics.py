"""Мерила «глазами оператора» (запись F9 2026-11-10) считают то, что названо: проверка на рисованных прогонах."""

import math

from loop import Trace, run
from metrics import (
    command_lag_share,
    off_frame_share,
    outside_zone_share,
    overtake_max,
    stop_delay_max,
    zone_exit_depth,
    zoom_moving_share,
    zoom_pumps_per_min,
)


def trace(T=60.0, dt=0.04, **series):
    """Прогон из функций времени: img_x, img_y, cam_zoom, seen, codes, applied."""
    tr = Trace()
    n = round(T / dt)
    tr.t = [i * dt for i in range(n)]
    defaults = dict(img_x=lambda t: 0.5, img_y=lambda t: 0.25, cam_zoom=lambda t: 0.0, seen=lambda t: True,
                    codes=lambda t: (50, 50, 50), applied=lambda t: (50, 50))
    for name, fn in {**defaults, **series}.items():
        setattr(tr, name, [fn(t) for t in tr.t])
    return tr


def test_zoom_pumps_counts_out_and_back_cycles():
    # два цикла «отъехал вдвое и вернулся» за минуту и мелкая рябь зума, которая циклом не считается
    def zoom(t):
        dip = math.log(2) * max(0.0, 1 - abs(t - 15) / 4) + math.log(2) * max(0.0, 1 - abs(t - 40) / 4)
        return 1.0 - dip + 0.02 * math.sin(t)

    assert zoom_pumps_per_min(trace(cam_zoom=zoom), t0=0.0) == 2.0
    assert zoom_pumps_per_min(trace(cam_zoom=lambda t: 0.02 * math.sin(t)), t0=0.0) == 0.0


def test_zoom_moving_share_is_time_zoom_changes():
    tr = trace(cam_zoom=lambda t: 0.25 * min(t, 15.0))  # едет первые 15 с из 60
    assert abs(zoom_moving_share(tr, t0=0.0) - 0.25) < 0.01


def test_off_frame_share_counts_lost_and_edge_time():
    tr = trace(img_x=lambda t: 0.95 if 10 <= t < 16 else 0.5, seen=lambda t: not 30 <= t < 33)
    assert abs(off_frame_share(tr, t0=0.0) - 9 / 60) < 0.01


def test_overtake_is_how_far_camera_passed_walker():
    # человек идёт вправо с 5-й по 10-ю секунду; на 9–11-й секундах он на 0,3 кадра ЛЕВЕЕ метки — камера обогнала
    def world(t):
        return 0.2 * (min(t, 10.0) - 5.0) if t > 5 else 0.0

    tr = trace(T=20, img_x=lambda t: 0.2 if 9 <= t < 11 else (0.8 if 6 <= t < 7 else 0.5))
    assert abs(overtake_max(tr, world) - 0.3) < 1e-6  # отставание (0,8 на 6-й секунде) проскоком не считается
    assert overtake_max(trace(T=20), world) == 0.0


def test_command_lag_and_stop_delay():
    # регулятор хочет код 60 с 10-й секунды, камера получает его на 10,5; стоп решён на 20-й, дошёл на 21,2
    tr = trace(T=30, codes=lambda t: (60 if 10 <= t < 20 else 50, 50, 50),
               applied=lambda t: (60 if 10.5 <= t < 21.2 else 50, 50))
    assert abs(stop_delay_max(tr) - 1.2) < 0.05
    assert abs(command_lag_share(tr, t0=0.0) - 0.5 / 10) < 0.01  # доля времени, когда поворот должен ехать


def test_outside_zone_share_counts_only_beyond_boundary():
    # обычная зона ±0,22 от метки 0,5: 6 с человек на 0,75 (за границей), ещё 6 с — на 0,70 (внутри)
    tr = trace(img_x=lambda t: 0.75 if 10 <= t < 16 else (0.70 if 20 <= t < 26 else 0.5))
    assert abs(outside_zone_share(tr, t0=0.0) - 6 / 60) < 0.01
    tr.zone, tr.anchor = (0.11, 0.09), (0.38, 0.25)  # плотная зона у метки слева: 0,5 уже за границей
    assert outside_zone_share(tr, t0=0.0) == 1.0


def test_zone_exit_depth_is_distance_past_boundary():
    tr = trace(img_x=lambda t: 0.20 if 10 <= t < 11 else 0.6)
    assert abs(zone_exit_depth(tr, t0=0.0) - 0.08) < 1e-6  # 0,30 от метки при границе 0,22
    assert zone_exit_depth(trace(img_x=lambda t: 0.7), t0=0.0) == 0.0


def test_u_drop_shrinks_measured_size_only():
    # человек на 2–4-й секунде встал боком: замер размера падает до 0,3, нос в замере ниже, а сам человек тот же
    tr = run(lambda t: 0.5, T=5.0, u_drops=((2.0, 2.0, 0.3),))
    before = [m for m in tr.meas if 1.0 <= m[0] < 2.0]
    during = [m for m in tr.meas if 2.0 <= m[0] < 4.0]
    assert during and all(abs(m[3] / tr.u[round(m[0] / 0.04)] - 0.3) < 0.02 for m in during)
    assert all(abs(m[3] / tr.u[round(m[0] / 0.04)] - 1.0) < 0.02 for m in before)
    shift = 0.5 * 0.19 * (1 - 0.3)
    assert all(abs((m[2] - tr.img_y[round(m[0] / 0.04)]) - shift * tr.u[round(m[0] / 0.04)] / 0.19) < 0.01 for m in during)
    assert min(tr.u) > 0.18  # настоящий размер не падал (зум мог только наехать)
