"""Оценка человека: положение, скорость, центр переминания, вес «идёт»."""

import random

import pytest

from ptztracker.control.person_estimator import PersonEstimator
from ptztracker.sim import person_motion as pm

DT = 1 / 14


def feed(fn, T, noise=0.004, k=1.0, seed=0, est=None, dt=DT, zone=1.0):
    est = est or PersonEstimator()
    rng = random.Random(seed)
    rows = []
    n = 0
    while n * dt < T:
        t = n * dt
        est.update(t, fn(t) + rng.gauss(0.0, noise) / k, k, zone)
        rows.append((t, est.walk, est.center, est.vel, est.pos, est.sway))
        n += 1
    return rows


def walking(v, t0=3.0, t1=8.0):
    return lambda t: 0.5 + pm.walk_position(t, t0, t1, v) + min(0.015, 0.05 * v) * pm.phase_fade(max(0.0, t - t0), t1 - t0) * pm.walk_bob(t)


@pytest.mark.parametrize("amp", [0.02, 0.05, 0.08, 0.12])
@pytest.mark.parametrize("seed", [0.0, 2.0, 4.0])
def test_sway_is_not_walking(amp, seed):
    rows = feed(lambda t: 0.5 + pm.sway(t, amp, seed), 30)
    late = [r[1] for r in rows if r[0] > 4]
    limit = 0.15 if amp >= 0.12 else 0.05  # шатается заметно: короткие всплески веса камеру не сдвигают (тест на стенде)
    assert sum(1 for w in late if w > 0.3) / len(late) <= limit


@pytest.mark.parametrize("amp", [0.05, 0.08, 0.12])
def test_center_holds_middle_of_sway(amp):
    rows = feed(lambda t: 0.5 + pm.sway(t, amp), 30)
    assert all(abs(r[2] - 0.5) <= max(0.03, 0.35 * amp) for r in rows if r[0] > 6)


def test_noise_alone_is_not_walking():
    rows = feed(lambda t: 0.5, 15, noise=0.006)
    assert max(r[1] for r in rows if r[0] > 1) < 0.2


@pytest.mark.parametrize("v", [0.15, 0.31, 0.5, 0.77])
def test_walking_recognised_quickly(v):
    rows = feed(walking(v), 8)
    limit = 3.0 + (1.7 if v < 0.2 else 0.9)  # от первого шага, в том числе разгон человека 0,5 с
    assert any(t <= limit and w > 0.8 for t, w, *_ in rows)


@pytest.mark.parametrize("v", [0.15, 0.31, 0.5, 0.77])
def test_walking_stays_recognised(v):
    rows = feed(walking(v), 8)
    mid = [w for t, w, *_ in rows if 4.5 <= t <= 7.3]
    assert sum(1 for w in mid if w > 0.5) / len(mid) >= 0.9


def test_stop_recognised_within_half_a_second():
    rows = feed(lambda t: 0.5 + 0.31 * max(0.0, min(t, 8.0) - 3.0), 11)
    t_fall = next(t for t, w, *_ in rows if t > 8.0 and w < 0.3)
    assert t_fall - 8.0 <= 0.45


def test_center_is_at_person_soon_after_stop():
    rows = feed(lambda t: 0.5 + 0.31 * max(0.0, min(t, 8.0) - 3.0), 11)
    target = 0.5 + 0.31 * 5
    r = next(r for r in rows if r[0] >= 8.8)
    assert abs(r[2] - target) <= 0.03


def test_velocity_matches_walk():
    rows = feed(walking(0.31), 8)
    vs = [r[3] for r in rows if 4.5 <= r[0] <= 7.3]
    assert abs(sum(vs) / len(vs) - 0.31) <= 0.03


def test_walking_recognised_at_low_frame_rate():
    # анализ может идти реже 11 к/с: скорость нельзя считать «нулевой» только потому, что замеры редкие
    est = PersonEstimator()
    n, dt = 0, 0.2
    walks = []
    while n * dt < 8.0:
        t = n * dt
        est.update(t, 0.5 + pm.walk_position(t, 3.0, 8.0, 0.31), 1.0)
        walks.append((t, est.walk))
        n += 1
    assert any(t <= 4.4 and w > 0.8 for t, w in walks)


def test_slow_steady_walk_is_not_mistaken_for_sway():
    # человек постоял, чуть переминаясь, и медленно пошёл (общий план): размах не растёт от ходьбы, вес поднимается
    rows = feed(lambda t: 0.5 + pm.sway(t, 0.02) * (t < 8) + pm.walk_position(t, 8.0, 18.0, 0.12), 18)
    assert any(t <= 11.5 and w > 0.8 for t, w, *_ in rows)
    mid = [w for t, w, *_ in rows if 12.0 <= t <= 17.0]
    assert sum(1 for w in mid if w > 0.5) / len(mid) >= 0.8
    assert all(sway < 0.1 for t, _, _, _, _, sway in rows)


def test_very_slow_walk_does_not_inflate_sway():
    # 0,08 кадра/с: вес переменчив, но размах не растёт — камера едет за центром и порог не уезжает
    rows = feed(lambda t: 0.5 + pm.sway(t, 0.02) * (t < 8) + pm.walk_position(t, 8.0, 18.0, 0.08), 18)
    assert all(sway <= 0.16 for t, _, _, _, _, sway in rows)


def test_sway_not_inflated_by_zoom_in():
    # широкий план, потом наезд ×8: размах в кадре не вырастает в 8 раз (запись F9: 0,2 кадра вместо 0,05)
    est = PersonEstimator()
    for i in range(14 * 20):
        t = i / 14
        k = 1.0 + 7.0 * min(1.0, t / 15.0)
        est.update(t, 0.5 + pm.sway(t, 0.05 / 8), k)
    assert est.sway <= 0.1


def test_fast_walk_recognised_after_sway_on_close_up():
    # стоит на крупном плане, потом быстро уходит (запись F9): вес поднимается раньше, чем человек дойдёт до края
    rows = feed(lambda t: 0.5 + pm.sway(t, 0.05) * (t < 8) + pm.walk_position(t, 8.0, 12.0, 0.73), 12)
    assert any(t <= 8.6 and w > 0.5 for t, w, *_ in rows)


@pytest.mark.parametrize("amp", [0.15, 0.2])
def test_strong_sway_is_not_walking(amp):
    # сильно шатается: вес «идёт» почти всё время низкий, размах выучен
    rows = feed(lambda t: 0.5 + pm.sway(t, amp), 40)
    late = [r[1] for r in rows if r[0] > 8]
    assert sum(1 for w in late if w > 0.5) / len(late) <= 0.15


def test_gap_restarts_estimate_at_new_place():
    est = PersonEstimator()
    for i in range(70):
        est.update(i / 14, 0.5, 1.0)
    est.update(70 / 14 + 1.0, 0.8, 1.0)  # пропал на 1 с и нашёлся в 0,3 кадра
    assert est.center == pytest.approx(0.8) and est.pos == pytest.approx(0.8)
    assert est.walk == 0.0 and est.vel == 0.0 and est.sway < 0.05


def test_slow_walk_on_close_up_is_recognised():
    # на крупном плане (k=4) медленный шаг 0,06 мира — 0,24 кадра/с: размах не должен «съедать» ходьбу
    rows = feed(lambda t: 0.5 + pm.walk_position(t, 3, 8, 0.06) + pm.sway(t, 0.008) * (t < 3), 8, k=4.0)
    assert any(t <= 4.6 and w > 0.8 for t, w, *_ in rows)
    assert all(s < 0.1 for t, _, _, _, _, s in rows if t < 8)


def test_thresholds_are_in_frame_units():
    # на теле (k=4) те же мировые шаги в 4 раза заметнее в кадре: переминание ±0,02 мира — как ±0,08 кадра
    rows = feed(lambda t: 0.5 + pm.sway(t, 0.02), 30, k=4.0)
    late = [r[1] for r in rows if r[0] > 4]
    assert sum(1 for w in late if w > 0.3) / len(late) <= 0.05
    walk = feed(lambda t: 0.5 + pm.walk_position(t, 3, 8, 0.31 / 4), 8, k=4.0)
    assert any(t <= 3.9 and w > 0.8 for t, w, *_ in walk)


# --- этап 2 (2026-11-11): поток MJPEG (1) даёт 25 замеров в секунду — прежние окна «в замерах» стали слишком короткими


def test_slow_walk_with_step_sway_stays_walking_at_25_fps():
    # шаг 0,19 кадра/с с качанием корпуса: человек идёт дальше, а не разворачивается — вес «идёт» не падает,
    # размах не «выучивается» (было: вес падал до нуля каждые 2 с, размах рос до 0,13, камера шла рывками)
    for seed in range(4):
        rows = feed(walking(0.19, t1=12.0), 12, seed=seed, dt=1 / 25)
        mid = [w for t, w, *_ in rows if 5.0 <= t <= 11.0]
        assert sum(1 for w in mid if w > 0.5) / len(mid) >= 0.95, seed
        assert max(sway for t, _, _, _, _, sway in rows if t >= 5.0) < 0.06, seed


def test_velocity_is_not_noisy_at_25_fps():
    # ровный шаг без качания: при 25 к/с три замера — 0,08 с, шум точек 0,004 давал в скорости ±0,1 кадра/с
    rows = feed(lambda t: 0.5 + pm.walk_position(t, 3.0, 12.0, 0.31), 12, dt=1 / 25)
    vs = [r[3] for r in rows if 5.0 <= r[0] <= 11.0]
    mean = sum(vs) / len(vs)
    assert abs(mean - 0.31) <= 0.02
    assert (sum((v - mean) ** 2 for v in vs) / len(vs)) ** 0.5 <= 0.035


def test_clear_deceleration_is_seen_without_waiting_for_long_window():
    # быстрый шаг 0,6 кадра/с, остановка за 0,5 с (с 7,5 до 8,0): к концу торможения оценка скорости уже мала
    # (в среднем по замерам на 7,9 и 8,0 с — не больше 0,155: замер 0,126; длинное окно с долей короткого давало 0,185)
    late = []
    for seed in range(8):
        rows = feed(lambda t: 0.5 + pm.walk_position(t, 3.0, 8.0, 0.6), 9, seed=seed, dt=1 / 25)
        late += [next(r[3] for r in rows if r[0] >= at) for at in (7.9, 8.0)]
    assert sum(late) / len(late) <= 0.155, late


def test_calm_stander_is_recognised_walking_before_he_is_far():
    # стоял спокойно и пошёл 0,35 кадра/с: вес «идёт» поднимается, пока он не дальше 0,12 кадра от места
    # (было 0,14 — на 0,2 с позже: к ответу камеры человек выходил за границу обычной зоны)
    for seed in range(4):
        fn = walking(0.35, t0=3.0, t1=8.0)
        rows = feed(lambda t, fn=fn: fn(t) + pm.sway(t, 0.01) * (t < 3), 6, seed=seed, dt=1 / 25)
        t_seen = next(t for t, w, *_ in rows if t > 3.0 and w > 0.5)
        assert fn(t_seen) - 0.5 <= 0.12, (seed, t_seen, fn(t_seen) - 0.5)


def test_prediction_is_not_used_for_known_swayer():
    # кто заметно шатается, так же быстро идёт обратно: прогноз «уйдёт» для него не действует (порог — по положению)
    rows = feed(lambda t: 0.5 + pm.sway(t, 0.2), 40, dt=1 / 25)
    late = [r[1] for r in rows if r[0] > 8]
    assert sum(1 for w in late if w > 0.3) / len(late) <= 0.05


# --- этап 2: пороги «ушёл от места» — в долях зоны оператора ---


def test_tight_zone_recognises_walking_earlier_and_loose_later():
    # тот, кто заметно переминался (±0,08) и пошёл 0,2 кадра/с: в плотной зоне (×0,5) «идёт» замечается раньше и
    # ближе к месту, в свободной (×1,8) — позже; в обычной — как раньше
    fn = lambda t: 0.5 + pm.sway(t, 0.08) * (t < 8) + pm.walk_position(t, 8.0, 16.0, 0.2)  # noqa: E731
    seen = {}
    for zone in (0.5, 1.0, 1.8):
        rows = feed(fn, 16, zone=zone, dt=1 / 25)
        seen[zone] = next(t for t, w, *_ in rows if t > 8.0 and w > 0.5)
    assert seen[0.5] + 0.1 <= seen[1.0] <= seen[1.8] - 0.15, seen
    usual = next(t for t, w, *_ in feed(fn, 16, dt=1 / 25) if t > 8.0 and w > 0.5)
    assert seen[1.0] == usual


def test_tight_zone_follows_sway_that_loose_zone_ignores():
    # шатание ±0,20: в плотной зоне (±0,11) это уход от места — на размахах вес «идёт» поднимается; в свободной
    # (±0,40) и в обычной — нет. Удержать такого человека в плотной зоне камера всё равно не может: при её отклике
    # 0,6–0,8 с ехать за шатанием с периодом 3 с хуже, чем стоять у середины (стенд 2026-11-11) — об этом плашка зоны.
    share = {}
    for zone in (0.5, 1.0, 1.8):
        rows = feed(lambda t: 0.5 + pm.sway(t, 0.2), 40, zone=zone, dt=1 / 25)
        late = [r[1] for r in rows if r[0] > 8]
        share[zone] = sum(1 for w in late if w > 0.5) / len(late)
    assert share[0.5] >= 0.1 and share[1.0] <= 0.02 and share[1.8] <= 0.02, share


# --- площадка 2026-11-13: замеру верим не всякому (бракованный кадр, пачка кадров, прыжок цели) ---


def _stand(p, t0=0.0, n=25, x=0.0, dt=0.085):
    for i in range(n):
        p.update(t0 + i * dt, x, 1.0)
    return t0 + n * dt


def test_one_glitched_measurement_is_ignored():
    """Нейросеть на один кадр поставила человека в 0,45 кадра от места: ни положение, ни скорость не меняются."""
    p = PersonEstimator()
    t = _stand(p)
    p.update(t, 0.45, 1.0)
    assert abs(p.pos) < 1e-6 and p.vel == 0.0 and not p.settling
    p.update(t + 0.085, 0.002, 1.0)  # следующий кадр — снова на месте
    assert abs(p.pos) < 0.01 and abs(p.vel) < 0.05 and not p.settling


def test_frames_from_a_burst_do_not_fake_speed():
    """После затора кадры приходят пачкой, через миллисекунды: сдвиг 0,02 за 5 мс — это не 4 кадра/с."""
    p = PersonEstimator()
    t = _stand(p)
    p.update(t + 0.4, 0.02, 1.0)   # затор 0,4 с
    p.update(t + 0.405, 0.04, 1.0)  # и два кадра подряд
    p.update(t + 0.41, 0.06, 1.0)
    assert abs(p.vel) < 0.3


def test_fast_steady_walker_is_not_taken_for_a_jump():
    """Крупный план: человек идёт 2,5 кадра/с (в кадре) — 0,21 кадра за замер при 12 к/с. Это ход, а не скачок."""
    p = PersonEstimator()
    t, x, k = 0.0, 0.0, 3.0
    for i in range(40):
        v = min(2.5, 6.0 * i * 0.085) / k  # разгон за 0,4 с, мировые единицы
        x += v * 0.085
        t += 0.085
        p.update(t, x, k)
        assert not p.settling, i
    assert abs(p.pos - x) * k < 0.2 and p.vel * k > 1.5


def test_confirmed_jump_is_a_new_place_but_not_trusted_at_once():
    """Цель перешла на другого человека: второй замер подтверждает новое место — оценка начинается заново, и 0,6 с
    камера за ней не идёт (settling)."""
    p = PersonEstimator()
    t = _stand(p)
    p.update(t, 0.4, 1.0)
    p.update(t + 0.085, 0.41, 1.0)
    assert abs(p.pos - 0.41) < 1e-6 and p.vel == 0.0 and p.settling
    for i in range(2, 10):
        p.update(t + i * 0.085, 0.41, 1.0)
    assert not p.settling  # продержалась 0,6 с — верим


def test_return_after_a_gap_on_the_same_course_is_trusted():
    """Шёл 0,5 кадра/с, на 0,6 с скрылся за соседом и нашёлся там, куда шёл: это он — ведём сразу. А нашёлся в стороне
    от своего хода — сначала присмотреться."""
    def walker():
        p, t, x = PersonEstimator(), 0.0, 0.0
        for _ in range(30):
            t += 0.085
            x += 0.5 * 0.085
            p.update(t, x, 1.0)
        return p, t, x

    p, t, x = walker()
    p.update(t + 0.6, x + 0.3, 1.0)
    assert not p.settling
    p, t, x = walker()
    p.update(t + 0.6, x - 0.4, 1.0)
    assert p.settling
