"""Цели «глазами оператора»: то, что оператор увидел на записи F9 2026-11-10 и чего не ловили прежние мерила.

За две минуты слежения на крупном плане: 8 раз зум «отъехал в 2–4 раза и вернулся», три раза камера проскочила
человека на 0,4 кадра, 10 % времени человек был у края кадра или потерян.

Маршруты случайные и разные, чтобы поведение не подгонялось под один заученный путь (требование оператора):
- TUNE — на них смотрят при настройке регулятора (первый — маршрут из записи F9);
- HELD_OUT — отложенные: при настройке на них не смотрят, они только подтверждают, что найденное поведение общее.
Каждая сцена — ещё и с движением по диагонали: камеры висят, как позволяет площадка, и проход вдоль сцены в кадре
идёт наискось, а человек при этом приближается или удаляется (наклон и размер меняются вместе с поворотом).
"""

from functools import lru_cache

import pytest
from loop import run
from metrics import off_frame_share, overtake_max, zoom_pumps_per_min

from ptztracker.sim import person_motion as pm

FRAME_M = 1.7  # ширина кадра «Пояса» при человеке в 14 м
ANCHOR_Y = 0.25
BENCH = dict(adapt=True, cam_he42=True, channel=True, apc_period=0.75, gz_period=0.75, noise=0.004, zoom_pos=0xD54,
             in_frame_only=True, edge_fallback=True, T=60)
SEEDS = {"настроечные": (906744089, 100, 101, 102, 103, 104, 105, 106), "отложенные": tuple(range(9000, 9012))}
# (наклон пути: доли высоты кадра на ширину кадра; глубина: во сколько раз меняется размер на ширину кадра; отказы камеры)
SCENES = {
    "ровно": (0.0, 0.0, 0.0),
    "ровно, отказы камеры": (0.0, 0.0, 0.16),
    "диагональ": (0.4, 0.15, 0.0),
    "диагональ обратная, отказы камеры": (-0.4, -0.15, 0.16),
    "диагональ крутая": (0.8, 0.25, 0.0),
    "Пояс · слева, диагональ": (0.4, 0.15, 0.0),
}
COMP = {"Пояс · слева, диагональ": "waist_left"}  # крупность сцены, если не «Пояс · центр»
# Цель поставлена, но на этом стенде не достигнута (замеры и причины — в конце плана
# docs/superpowers/plans/2026-11-10-stage2-zone-boundary-regulator.md): от шага или остановки человека до ответа камеры
# проходит 0,6–0,8 с, и на крупном плане он за это время уходит на 0,2–0,5 кадра. strict: цель достигнута — метку снять.
NOT_REACHED = pytest.mark.xfail(strict=True, reason="цель этапа 2 не достигнута: предел стенда (0,6–0,8 с до ответа камеры)")
# Замер стоит у самого порога и переходит его от порядка команд в общей очереди, а не от поведения (BACKLOG, «Два
# порога, стоявшие впритык»). Порог не сдвинут: оператор решит после проверки на площадке. Не strict — на другой машине
# замер может оказаться и по эту сторону порога.
ON_THE_EDGE = pytest.mark.xfail(strict=False, reason="замер у самого порога, ждёт решения оператора после площадки")


def cases(pairs, not_reached, on_the_edge=()):
    """Параметры теста; те, где цель пока не достигнута, помечены NOT_REACHED, у самого порога — ON_THE_EDGE."""
    marks = {**{p: ON_THE_EDGE for p in on_the_edge}, **{p: NOT_REACHED for p in not_reached}}
    return [pytest.param(*p, marks=marks[p]) if p in marks else p for p in pairs]


@lru_cache(maxsize=None)
def runs(scene: str, seeds: str):
    slope, depth, er2 = SCENES[scene]
    out = []
    for seed in SEEDS[seeds]:
        route = pm.RandomRoute(seed)

        def x(t, r=route):
            return 0.5 + r.x(t) / FRAME_M

        tr = run(x, world_y=lambda t, x=x: ANCHOR_Y + slope * (x(t) - 0.5),
                 world_u=lambda t, x=x: 0.19 * (1 + depth * (x(t) - 0.5)), seed=seed % 1000, er2=er2,
                 comp=COMP.get(scene, "waist_center"), **BENCH)
        out.append((tr, x))
    return out


def avg(values):
    values = list(values)
    return sum(values) / len(values)


ALL = [(scene, seeds) for scene in SCENES for seeds in SEEDS]
DIAGONAL = [(scene, seeds) for scene, seeds in ALL if SCENES[scene][0]]


@pytest.mark.parametrize("scene,seeds", ALL)
def test_watchdog_is_quiet(scene, seeds):
    assert not any("diverged" in tr.state for tr, _ in runs(scene, seeds))


@pytest.mark.parametrize("scene,seeds", cases(ALL, {("диагональ крутая", "отложенные")},
                                               on_the_edge={("диагональ крутая", "настроечные")}))
def test_zoom_does_not_pump(scene, seeds):
    """Зум не ходит «отъехал — наехал»: в среднем не чаще раза в минуту (в записи — 4 раза в минуту).
    Достигнуто везде (0,4–0,8), кроме крутой диагонали на отложенных маршрутах: там человек чаще уходит из кадра по
    высоте, и отъезд «от края» срабатывает чаще. Крутая диагональ на настроечных — у самого порога: с запасом
    «как на макете» 1,03 (8 циклов на 8 маршрутов вместо 7), при прежнем запасе было 0,9."""
    assert avg(zoom_pumps_per_min(tr) for tr, _ in runs(scene, seeds)) <= 1.0


@pytest.mark.parametrize("scene,seeds", cases(ALL, set(ALL)))
def test_camera_does_not_overtake_walker(scene, seeds):
    """Камера не обгоняет человека: в среднем не больше 0,12 кадра, ни разу больше 0,22 (в записи — 0,42–0,44).
    Не достигнуто: стало 0,23–0,35 в среднем (было 0,36–0,42)."""
    over = [overtake_max(tr, x, anchor=tr.anchor[0]) for tr, x in runs(scene, seeds)]
    assert avg(over) <= 0.12 and max(over) <= 0.22


@pytest.mark.parametrize("scene,seeds", cases(ALL, {p for p in ALL if p[0] != "диагональ крутая"}))
def test_person_stays_inside_frame(scene, seeds):
    """Человек у бокового края или вне кадра — не больше 1,5 % времени (в записи — 10 %).
    Достигнуто на крутой диагонали (0,6–1,2 %); на остальных сценах — 1,8–3,0 % (было 4,4–6,2 %)."""
    assert avg(off_frame_share(tr) for tr, _ in runs(scene, seeds)) <= 0.015


@pytest.mark.parametrize("scene,seeds", cases(DIAGONAL, set(DIAGONAL)))
def test_tilt_follows_diagonal_walk(scene, seeds):
    """Проход наискось: голова не уходит от метки по вертикали дальше 0,2 высоты кадра (было до 0,5–0,76: за кадр).
    Не достигнуто: стало 0,22–0,34 на диагоналях и 0,41–0,50 на крутой."""
    worst = [max(abs(y - ANCHOR_Y) for t, y, seen in zip(tr.t, tr.img_y, tr.seen) if t > 2 and seen)
             for tr, _ in runs(scene, seeds)]
    assert max(worst) <= 0.2
