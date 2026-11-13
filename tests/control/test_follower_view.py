"""Вид камеры на момент кадра (для области сцены) — на симуляторе, в реальном времени."""

import time

import pytest
from test_follower import select_person, sim_session, wait_until

from ptztracker.camera.session import VIEW_MAX_AGE_S
from ptztracker.control.stage_area import StageArea

pytestmark = pytest.mark.slow

AREA = StageArea(-180.0, 180.0, -5.0, 90.0)


def _apc(s):
    return sum(1 for c in s.sim.received if c.startswith("#APC"))


def test_area_keeps_position_polled_without_recording_and_follow():
    s = sim_session(calibrated=True, script="stand")
    try:
        s.recorder.active = False
        time.sleep(0.6)
        n = _apc(s)
        time.sleep(4.5)
        assert _apc(s) == n, "без записи, слежения и области положение не спрашивают"
        assert s.stage_view(time.monotonic()) is None
        s.set_stage_area(AREA)
        assert wait_until(lambda: s.stage_view(time.monotonic()) is not None, 5)
        v = s.stage_view(time.monotonic())
        assert v.pan_deg == pytest.approx(s.sim.pan_deg, abs=0.05) and v.tilt_deg == pytest.approx(s.sim.tilt_deg, abs=0.05)
        assert v.hfov_deg == pytest.approx(s.sim.hfov_deg(), rel=0.01)
    finally:
        s.stop()


def test_unknown_view_disables_area():
    """Ответа о положении нет дольше VIEW_MAX_AGE_S — вид неизвестен, а не «как был»."""
    s = sim_session(calibrated=True, script="stand")
    try:
        s.set_stage_area(AREA)
        assert wait_until(lambda: s.stage_view(time.monotonic()) is not None, 5)
        s.follower.polling_paused = True
        now = time.monotonic()
        assert s.stage_view(now + VIEW_MAX_AGE_S + 2.5) is None
    finally:
        s.stop()


def test_manual_action_forgets_view_until_next_answer():
    s = sim_session(calibrated=True, script="stand")
    try:
        s.set_stage_area(AREA)
        assert wait_until(lambda: s.stage_view(time.monotonic()) is not None, 5)
        s.set_pan_tilt_velocity(0.3, 0.0)
        assert s.stage_view(time.monotonic()) is None, "камеру двигают вручную — старое положение неверно"
        s.set_pan_tilt_velocity(0.0, 0.0)
        assert wait_until(lambda: s.stage_view(time.monotonic()) is not None, 6)
        assert s.stage_view(time.monotonic()).pan_deg == pytest.approx(s.sim.pan_deg, abs=0.05)
    finally:
        s.stop()


def test_world_point_is_stable_on_sim():
    """Слежение ведёт идущего: вид по модели между ответами #APC не расходится с настоящим положением камеры."""
    s = sim_session(composition="waist_center", calibrated=True, script="walk")
    try:
        s.set_stage_area(AREA)
        select_person(s)
        assert s.follower.enable()
        worst, zoom, hf, n, end = 0.0, 0.0, [], 0, time.monotonic() + 8.0
        while time.monotonic() < end:
            time.sleep(0.1)
            now = time.monotonic()
            v = s.stage_view(now + s.follower.video_latency())  # кадр, пришедший позже, — камера «сейчас»
            if v is None:
                continue
            n += 1
            worst = max(worst, abs(v.pan_deg - s.sim.pan_deg) / v.hfov_deg, abs(v.tilt_deg - s.sim.tilt_deg) / v.hfov_deg)
            zoom = max(zoom, abs(v.hfov_deg / s.sim.hfov_deg() - 1))
            hf.append(s.sim.hfov_deg())
        assert n > 40 and worst < 0.05 and zoom < 0.06, (n, worst, zoom)
        assert max(hf) / min(hf) > 1.5, "за прогон камера должна была наехать — иначе зум не проверен"
    finally:
        s.stop()
