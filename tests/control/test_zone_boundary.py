"""Цели этапа 2: зона оператора — граница, из которой человека не выпускают (спека 2026-11-10).

Стенд тот же, что в test_operator_eye (камера как HE42 через настоящий планировщик), плюс то, чего в нём не было:
три потока (задержка видео и частота замеров), камера быстрее модели (живая HE40), зоны разного размера, человек,
который встаёт боком и наклоняется (замер размера падает). Числа подбираются только на настроечных маршрутах.
"""

from functools import lru_cache

import pytest
from loop import run
from metrics import (
    moving_share,
    off_frame_share,
    outside_zone_share,
    overtake_max,
    zone_exit_depth,
    zoom_moving_share,
    zoom_pumps_per_min,
)
from test_operator_eye import ANCHOR_Y, BENCH, FRAME_M, NOT_REACHED, SEEDS, avg

from ptztracker.sim import person_motion as pm

# поток: на сколько опаздывает видео и как часто приходят замеры (площадка 2026-11-10)
STREAMS = {
    "mjpeg1": dict(latency=0.15, meas_dt=1 / 25),
    "mjpeg1_две_камеры": dict(latency=0.15, meas_dt=1 / 15),
    "mjpeg2": dict(latency=0.25, meas_dt=1 / 12),
}
HE40 = dict(cam_dead_time=0.05, cam_tau=0.05)  # живая HE40 отвечает быстрее, чем считает калибровка (0,08 и 0,12)
ZONES = {"плотная": 0.5, "обычная": 1.0, "свободная": 1.8}  # размер зоны оператора
CALM_M_S = 0.35 * FRAME_M  # спокойный шаг: не быстрее 0,35 кадра/с на «Поясе»


@lru_cache(maxsize=None)
def runs(stream: str = "mjpeg1", zone: str = "обычная", seeds: str = "настроечные", fast_camera: bool = False,
         turns: bool = False, calm: bool = False):
    out = []
    for seed in SEEDS[seeds]:
        route = pm.RandomRoute(seed, max_speed=CALM_M_S) if calm else pm.RandomRoute(seed)

        def x(t, r=route):
            return 0.5 + r.x(t) / FRAME_M

        tr = run(x, world_y=lambda t: ANCHOR_Y, seed=seed % 1000, zones={"waist_center": [0.5, ANCHOR_Y, ZONES[zone]]},
                 u_drops=pm.turn_events(seed, BENCH["T"]) if turns else (), **(HE40 if fast_camera else {}),
                 **STREAMS[stream], **BENCH)
        out.append((tr, x))
    return out


@NOT_REACHED
@pytest.mark.parametrize("stream", STREAMS)
def test_person_stays_inside_operator_zone(stream):
    """Человек за границей обычной зоны — не больше 3 % времени. Не достигнуто: 12–13 % (было 19–20 %)."""
    assert avg(outside_zone_share(tr) for tr, _ in runs(stream)) <= 0.03


@NOT_REACHED
@pytest.mark.parametrize("stream", STREAMS)
def test_calm_walker_is_never_deep_outside_zone(stream):
    """На спокойном шаге (до 0,35 кадра/с) человек ни разу не выходит за границу глубже 0,06 кадра.
    Не достигнуто: худший выход 0,16–0,20 (было 0,19–0,26) — тот, кто сильно шатался у края зоны и пошёл."""
    assert max(zone_exit_depth(tr) for tr, _ in runs(stream, calm=True)) <= 0.06


@NOT_REACHED
def test_zone_size_changes_camera_behaviour():
    """Чем плотнее зона, тем больше времени камера едет: шаг между размерами — не меньше четверти.
    Не достигнуто: 51 / 46 / 42 % (шаг 10 %; было 54 / 50 / 48). Размер зоны меняет, когда камера трогается
    (test_tight_zone_starts_camera_earlier), но на этих маршрутах человек идёт 36 % времени при любой зоне."""
    tight, normal, loose = (avg(moving_share(tr, 2.0) for tr, _ in runs(zone=z)) for z in ZONES)
    assert tight >= 1.25 * normal and normal >= 1.25 * loose


def test_zoom_ignores_turns_and_bows():
    """Человек встаёт боком и наклоняется — зум не ходит за этим: «насос» не чаще раза в минуту, едет ≤ 15 % времени."""
    trs = [tr for tr, _ in runs(turns=True)]
    assert avg(zoom_pumps_per_min(tr) for tr in trs) <= 1.0
    assert avg(zoom_moving_share(tr) for tr in trs) <= 0.15


@NOT_REACHED
@pytest.mark.parametrize("stream", STREAMS)
def test_goals_hold_on_fast_camera(stream):
    """Камера отвечает быстрее модели (живая HE40 при прежней калибровке): не обгоняет и не теряет человека.
    Не достигнуто: обгон 0,20–0,21 в среднем при цели 0,12 (было 0,25–0,27), у края 1,2–1,7 % при цели 1,5 %."""
    rs = runs(stream, fast_camera=True)
    over = [overtake_max(tr, x) for tr, x in rs]
    assert avg(over) <= 0.12 and max(over) <= 0.22
    assert avg(off_frame_share(tr) for tr, _ in rs) <= 0.015


# --- размер зоны меняет работу слежения (задача 5) ---

GOALS_BENCH = dict(adapt=True, cam_he42=True, channel=True, apc_period=0.75, gz_period=0.75, noise=0.004)


def zone(size_w, size_h=None, x=0.5):
    return {"waist_center": [x, ANCHOR_Y, size_w, size_w if size_h is None else size_h]}


def test_tight_zone_starts_camera_earlier():
    """Тот, кто переминался (±0,08) и пошёл 0,2 кадра/с: в плотной зоне камера трогается раньше и отпускает его от
    точки меньше, в свободной — позже и дальше."""
    def fn(t):
        return 0.5 + pm.sway(t, 0.08) * (t < 8) + pm.walk_position(t, 8.0, 16.0, 0.2)

    start, peak = {}, {}
    for name, size in ZONES.items():
        trs = [run(fn, T=14, seed=seed, zones=zone(size), **GOALS_BENCH, **STREAMS["mjpeg1"]) for seed in range(4)]
        start[name] = avg(next(t for t, v in zip(tr.t, tr.cam_v) if t > 8.3 and v > 0.08) for tr in trs)
        peak[name] = avg(max(x - 0.5 for t, x in zip(tr.t, tr.img_x) if t > 8) for tr in trs)
    assert start["плотная"] + 0.1 <= start["обычная"] <= start["свободная"] - 0.1, start
    assert peak["плотная"] + 0.02 <= peak["обычная"] <= peak["свободная"] - 0.02, peak


def test_zone_change_while_following_is_smooth():
    """Оператор тянет зону во время слежения (точка 0,50 → 0,62, зона плотнее): камера доезжает до новой точки
    плавно — слежение не выключается, ускорение камеры не больше обычного, без разворотов."""
    from ptztracker.control.zones import framing

    def drag(t, ctrl, out):
        if 6.0 <= t <= 6.6:  # тянет 0,6 с
            k = (t - 6.0) / 0.6
            comp, effort = framing("waist_center", 0.5, zone(1.0 - 0.4 * k, 1.0, x=0.5 + 0.12 * k))
            ctrl.set_composition(comp)
            ctrl.set_effort(effort)

    tr = run(lambda t: 0.5 + pm.sway(t, 0.03), T=14, seed=0, controller_hook=drag, **GOALS_BENCH, **STREAMS["mjpeg1"])
    assert "diverged" not in tr.state and "idle" not in tr.state[25:]
    after = [(t, v) for t, v in zip(tr.t, tr.cam_v) if t > 6.0]
    acc = [abs(b[1] - a[1]) / 0.12 for a, b in zip(after, after[3:])]
    assert max(acc) <= 1.5, max(acc)  # a_max «Обычно» — 1,0 кадра/с² (торможение — до 2,5; здесь до него не доходит)
    signs = [1 if v > 0 else -1 for _, v in after if abs(v) > 0.03]
    assert sum(1 for a, b in zip(signs, signs[1:]) if a != b) <= 1
    assert abs(avg(tr.img_x[-50:]) - 0.62) <= 0.05  # человек у новой точки
