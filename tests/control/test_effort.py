from ptztracker.control.effort import EffortProfile


def test_points_and_interpolation():
    soft, mid, sharp = EffortProfile.at(0), EffortProfile.at(0.5), EffortProfile.at(1)
    assert (soft.dead_x, soft.gain, soft.v_max, soft.zoom_max) == (0.07, 0.9, 0.30, 0.15)
    assert (mid.dead_x, mid.gain, mid.v_max, mid.a_max) == (0.05, 1.4, 0.50, 0.45)
    assert (sharp.dead_y, sharp.soft_x, sharp.lead, sharp.zoom_gain) == (0.03, 0.18, 0.06, 1.2)
    q = EffortProfile.at(0.25)
    assert abs(q.gain - (0.9 + 1.4) / 2) < 1e-9 and abs(q.v_max - 0.40) < 1e-9
    assert EffortProfile.at(-1) == soft and EffortProfile.at(2) == sharp
