"""Зум без «насоса» (этап 2, задача 6): наезд — когда человек в зоне и размер устойчиво не тот; отъезд раньше срока —
только если человек уходит из самого кадра, а поворотом не успеть.

Запись F9 2026-11-10: за две минуты зум 8 раз «отъехал в 2–4 раза и вернулся»; живой прогон — 3,4 цикла в минуту.
"""

import math

import pytest
from loop import run
from test_tracking_goals import BENCH

from ptztracker.control import framing
from ptztracker.control.camera_model import default_calibration
from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.control.framing import FramingController
from ptztracker.sim import person_motion as pm

MJPEG1 = dict(latency=0.15, meas_dt=1 / 25)


def first(tr, cond, t0=0.0):
    return next((t for t, z in zip(tr.t, tr.zoom) if t >= t0 and cond(z)), None)


def test_zoom_in_waits_for_steady_size_error():
    """Размер стал меньше нужного: наезд начинается не раньше, чем через 2 с (ZOOM_IN_PATIENCE_S), и доводит размер до цели.
    Короткий провал размера (1,5 с) наезда не вызывает вовсе."""
    lasting = run(lambda t: 0.5 + pm.sway(t, 0.02), world_u=lambda t: 0.19 if t < 5 else 0.12, T=30, **BENCH, **MJPEG1)
    t_in = first(lasting, lambda z: z > 0.02, 5.0)
    assert framing.ZOOM_IN_PATIENCE_S == 2.0
    assert t_in is not None and t_in - 5.0 >= 2.0, t_in
    assert lasting.u[-1] >= 0.165  # размер доведён (цель 0,19, мёртвая зона зума 12 %)
    brief = run(lambda t: 0.5 + pm.sway(t, 0.02), world_u=lambda t: 0.12 if 5 <= t < 6.5 else 0.19, T=14, **BENCH, **MJPEG1)
    assert max(brief.cam_zoom) <= 0.02


def test_zoom_out_for_too_big_person_is_quicker_than_zoom_in():
    """Человек стал крупнее нужного (подошёл): отъезд ждёт меньше, чем наезд, — крупный план режет голову."""
    tr = run(lambda t: 0.5 + pm.sway(t, 0.02), world_u=lambda t: 0.19 if t < 5 else 0.27, T=14, **BENCH, **MJPEG1)
    t_out = first(tr, lambda z: z < -0.02, 5.0)
    assert t_out is not None and 0.7 <= t_out - 5.0 <= 2.0, t_out  # ZOOM_OUT_PATIENCE_S … ZOOM_IN_PATIENCE_S


def test_no_zoom_in_while_person_outside_zone():
    """Наезд — только когда человек внутри зоны оператора. Человек в 0,15 кадра от точки и вдвое мельче нужного:
    в обычной зоне (±0,22) наезд идёт, в плотной (±0,11) — ждёт, пока камера его не подведёт."""
    def zoom_after_4s(zone_x):
        e = EffortProfile.at(0.5)
        ctrl = FramingController(COMPOSITIONS["waist_center"], e, default_calibration(), 0.15)
        ctrl._u = 0.5 * ctrl.composition.u_frac
        ctrl.x.reach, ctrl.x.zone, ctrl.y.zone = 0.15, zone_x, e.soft_y
        caps = ((ctrl.x, 1.0, zone_x), (ctrl.y, 1.0, e.soft_y))
        cmd = 0.0
        for i in range(100):
            cmd, _ = ctrl._zoom(i * 0.04, 0.04, True, e, 0x800, 0.0, caps)
        return cmd

    assert zoom_after_4s(0.22) > 0.05
    assert zoom_after_4s(0.11) == 0.0


@pytest.mark.parametrize("direction", [1, -1])
def test_escape_zoom_only_near_frame_edge(direction):
    """«Пояс · слева» (точка 0,38). Вправо человек уходит от точки на 0,3 (шаг 0,45 кадра/с), но до края кадра
    далеко — зум не трогаем: поворот догонит. Влево быстрым шагом (0,7) — он уходит за край кадра: отъезд есть."""
    def fn(t):
        return 0.38 + direction * (0.45 if direction > 0 else 0.7) * max(0.0, min(t, 4.0) - 3.0)

    tr = run(fn, T=8, comp="waist_left", in_frame_only=True, edge_fallback=True, **BENCH, **MJPEG1)
    far = max(direction * (x - 0.38) for t, x in zip(tr.t, tr.img_x) if 3.0 < t < 5.0)
    assert far >= 0.25, far  # человек правда ушёл далеко от точки
    widest = math.exp(min(tr.cam_zoom))
    if direction > 0:
        assert max(tr.img_x) < 0.94 and widest >= 0.97, (max(tr.img_x), widest)
    else:
        assert widest <= 0.9, widest


def test_after_escape_zoom_in_waits_for_calm():
    """После отъезда «от края» наезд возвращается не раньше, чем человек 12 с (ZOOM_CALM_AFTER_ESCAPE_S) спокойно в зоне."""
    def fn(t):
        return 0.5 + 0.8 * max(0.0, min(t, 4.5) - 3.0)  # убежал на 1,2 кадра и встал

    tr = run(fn, T=28, zoom_pos=0xC00, in_frame_only=True, edge_fallback=True, **BENCH, **MJPEG1)
    t_out = first(tr, lambda z: z < -0.05, 3.0)
    assert t_out is not None, "должен был отъехать: человек ушёл за край кадра"
    t_calm = next(t for t, x in zip(tr.t, tr.img_x) if t > 4.5 and abs(x - 0.5) < 0.22)
    t_in = first(tr, lambda z: z > 0.02, t_out)
    assert t_in is not None and t_in - t_calm >= 12.0 - 0.3, (t_calm, t_in)
