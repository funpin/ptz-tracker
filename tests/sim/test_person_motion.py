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


def test_random_route_same_seed_same_route_other_seed_differs():
    a, b, c = pm.RandomRoute(7), pm.RandomRoute(7), pm.RandomRoute(8)
    times = [i * 0.5 for i in range(240)]
    assert [a.x(t) for t in times] == [b.x(t) for t in times]
    assert [a.x(t) for t in times] != [c.x(t) for t in times]


@pytest.mark.parametrize("seed", range(12))
def test_random_route_stays_on_stage_and_has_no_jumps(seed):
    r = pm.RandomRoute(seed, half_width_m=2.5)
    xs = [r.x(i * 0.02) for i in range(6000)]
    assert max(abs(x) for x in xs) <= 2.5 + 0.45
    assert max(abs(b - a) for a, b in zip(xs, xs[1:])) <= 0.05


def test_random_routes_vary_direction_speed_and_sway():
    legs = [leg for seed in range(20) for r in [pm.RandomRoute(seed)] if r.x(120.0) is not None for leg in r.legs]
    walks = [leg for leg in legs if leg.walking]
    assert {leg.x1 > leg.x0 for leg in walks} == {True, False}
    speeds = [abs(leg.x1 - leg.x0) / leg.length for leg in walks]
    assert max(speeds) > 0.8 and min(speeds) < 0.4
    amps = [leg.sway for leg in legs if not leg.walking]
    assert max(amps) >= 0.25 and min(amps) <= 0.08


def test_swaying_route_has_strong_sway_most_of_the_time():
    r = pm.RandomRoute(3, swaying=True)
    r.x(200.0)
    stands = [leg for leg in r.legs if not leg.walking]
    assert sum(1 for leg in stands if leg.sway >= 0.25) / len(stands) >= 0.5


def test_stage_random_script_uses_seed():
    a, b = SyntheticStage(script="random", seed=1), SyntheticStage(script="random", seed=2)
    assert [a.person_x(i * 2.0) for i in range(60)] != [b.person_x(i * 2.0) for i in range(60)]
    assert SyntheticStage(script="big_sway").route is None


def test_stage_rejects_unknown_script():
    with pytest.raises(ValueError):
        SyntheticStage(script="nope")
