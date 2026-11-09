"""Оценка человека: положение, скорость, центр переминания, вес «идёт»."""

import random

import pytest

from ptztracker.control.person_estimator import PersonEstimator
from ptztracker.sim import person_motion as pm

DT = 1 / 14


def feed(fn, T, noise=0.004, k=1.0, seed=0, est=None):
    est = est or PersonEstimator()
    rng = random.Random(seed)
    rows = []
    n = 0
    while n * DT < T:
        t = n * DT
        est.update(t, fn(t) + rng.gauss(0.0, noise) / k, k)
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
    assert all(sway <= 0.14 for t, _, _, _, _, sway in rows)


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
    assert all(s * 4.0 < 0.1 for t, _, _, _, _, s in rows if t < 8)


def test_thresholds_are_in_frame_units():
    # на теле (k=4) те же мировые шаги в 4 раза заметнее в кадре: переминание ±0,02 мира — как ±0,08 кадра
    rows = feed(lambda t: 0.5 + pm.sway(t, 0.02), 30, k=4.0)
    late = [r[1] for r in rows if r[0] > 4]
    assert sum(1 for w in late if w > 0.3) / len(late) <= 0.05
    walk = feed(lambda t: 0.5 + pm.walk_position(t, 3, 8, 0.31 / 4), 8, k=4.0)
    assert any(t <= 3.9 and w > 0.8 for t, w, *_ in walk)
