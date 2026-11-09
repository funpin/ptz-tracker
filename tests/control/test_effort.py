from ptztracker.control.effort import EffortProfile


def test_points_and_interpolation():
    soft, mid, sharp = EffortProfile.at(0), EffortProfile.at(0.5), EffortProfile.at(1)
    assert (soft.dead_x, soft.gain, soft.v_max, soft.zoom_max) == (0.07, 1.2, 0.80, 0.15)
    assert (mid.dead_x, mid.gain, mid.v_max, mid.a_max, mid.j_max) == (0.05, 1.7, 1.20, 1.0, 4.0)
    assert (sharp.dead_y, sharp.soft_x, sharp.lead, sharp.zoom_gain) == (0.03, 0.18, 0.0, 1.2)
    q = EffortProfile.at(0.25)
    assert abs(q.gain - (1.2 + 1.7) / 2) < 1e-9 and abs(q.v_max - 1.0) < 1e-9
    assert EffortProfile.at(-1) == soft and EffortProfile.at(2) == sharp


def test_every_effort_can_catch_a_walking_speaker():
    """«Мягко ↔ Резко» меняет плавность и зоны, но не запрещает догонять: потолок не ниже скорости
    быстрой ходьбы на «Поясе» (~0,77 fw/s, часть 0, C)."""
    for x in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert EffortProfile.at(x).v_max >= 0.77
