"""Как двигается человек в симуляторе: переминание, разгон и торможение, качание на ходу."""

import math

import pytest

from ptztracker.sim import person_motion as pm
from ptztracker.sim.scene import SWAY_M, SyntheticStage


def test_sway_is_bounded_and_not_periodic_within_a_minute():
    values = [pm.sway(i * 0.05, 0.1) for i in range(1200)]
    assert max(abs(v) for v in values) <= 0.1
    assert max(values) - min(values) >= 0.1
    assert abs(sum(values) / len(values)) <= 0.02


def test_walk_position_has_ramps_and_reaches_full_speed():
    pos = lambda t: pm.walk_position(t, 1.0, 6.0, 0.4, 0.5)  # noqa: E731
    assert pos(1.0) == 0.0
    mid = (pos(3.1) - pos(3.0)) / 0.1
    assert mid == pytest.approx(0.4, abs=1e-6)
    assert (pos(1.1) - pos(1.0)) / 0.1 < 0.1  # разгон
    assert pos(6.0) == pytest.approx(0.4 * 4.5, abs=1e-9)
    assert pos(8.0) == pos(6.0)


def test_phase_fade_is_smooth_at_edges():
    assert pm.phase_fade(0.0, 5.0) == 0.0
    assert pm.phase_fade(2.5, 5.0) == 1.0
    assert pm.phase_fade(5.0, 5.0) == 0.0


@pytest.mark.parametrize("script", ["stand", "shuffle"])
def test_standing_person_sways_noticeably(script):
    st = SyntheticStage(script=script)
    xs = [st.person_x(i * 0.1) for i in range(600)]
    spread_m = (max(xs) - min(xs)) / st.person_ppm
    assert spread_m == pytest.approx(2 * SWAY_M[script], rel=0.35)


def test_walking_has_no_jump_at_phase_changes():
    st = SyntheticStage(script="lecture")
    steps = [abs(st.person_x(t + 0.02) - st.person_x(t)) / st.person_ppm for t in (i * 0.02 for i in range(1800))]
    assert max(steps) <= 0.05  # метров за 0,02 с: ~2,5 м/с — нет скачков на стыках отрезков


def test_walking_bobs_up_and_down():
    st = SyntheticStage(script="lecture")
    ys = [st.person_world_point(10.0 + i * 0.02, 0.0, 0.92)[1] for i in range(150)]
    assert max(ys) - min(ys) >= 0.01
    still = [st.person_world_point(2.0 + i * 0.02, 0.0, 0.92)[1] for i in range(150)]
    assert max(still) == pytest.approx(min(still), abs=1e-9)


def test_walk_bob_period_matches_step_rate():
    assert pm.walk_bob(0.0) == pytest.approx(0.0, abs=1e-9)
    assert pm.walk_bob(1 / pm.STEP_HZ) == pytest.approx(0.0, abs=1e-6)
    assert math.isclose(max(pm.walk_bob(i * 0.001) for i in range(1000)), 1.0, abs_tol=1e-3)
