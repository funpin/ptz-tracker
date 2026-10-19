import time

import numpy as np

from ptztracker.camera.session import CameraSession
from ptztracker.config import CameraConfig


def wait_until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def sim_session(**cfg):
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim", **cfg))
    s.start()
    assert wait_until(lambda: s.analyzer.latest() is not None and len(s.analyzer.latest().tracks) > 0, 4)
    return s


def select_person(s):
    tr = s.analyzer.latest().tracks[0]
    assert s.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2) is not None
    assert wait_until(lambda: s.analyzer.latest().target_state == "tracking", 2)


def last_pt(s):
    return [c for c in s.sim.received if c.startswith("#PTS")][-1]


def test_enable_requires_target():
    s = sim_session()
    try:
        assert s.follower.enable() is False
        select_person(s)
        assert s.follower.enable() is True and s.follower.enabled
    finally:
        s.stop()


def test_follows_walking_sim_person():
    s = sim_session(composition="full")
    try:
        select_person(s)
        assert s.follower.enable()
        errs, in_frame = [], []
        t_end = time.monotonic() + 10
        while time.monotonic() < t_end:
            fr = s.frames.latest()
            kp = fr.meta["people"][0]
            in_frame.append(kp[0, 2] > 0)
            if time.monotonic() > t_end - 6 and kp[0, 2] > 0:
                errs.append(abs(kp[0, 0] / fr.image.shape[1] - 0.5))
            time.sleep(0.1)
        assert all(in_frame), "человек выходил из кадра"
        assert np.mean(errs) < 0.08, f"средняя ошибка {np.mean(errs):.3f}"
    finally:
        s.stop()


def test_manual_joystick_disables_immediately():
    s = sim_session()
    try:
        select_person(s)
        s.follower.enable()
        time.sleep(1.0)
        s.set_pan_tilt_velocity(0.5, 0)
        assert not s.follower.enabled and s.follower.last_reason == "ручное управление"
        time.sleep(0.6)
        assert last_pt(s) == "#PTS7550"  # ручная команда, автоматика больше не шлёт
    finally:
        s.stop()


def test_emergency_stop_disables():
    s = sim_session()
    try:
        select_person(s)
        s.follower.enable()
        s.emergency_stop()
        assert not s.follower.enabled and s.follower.last_reason == "аварийный стоп"
    finally:
        s.stop()


def test_stale_analysis_stops_camera():
    s = sim_session()
    try:
        select_person(s)
        s.follower.enable()
        time.sleep(0.5)
        s.analyzer.stop()
        assert wait_until(lambda: not s.follower.enabled, 1.5)
        time.sleep(0.4)
        assert last_pt(s) == "#PTS5050"
    finally:
        s.stop()


def test_target_lost_stops_camera():
    s = sim_session(composition="full")
    try:
        select_person(s)
        s.follower.enable()
        time.sleep(2.0)
        s.analyzer.clear_target()
        assert wait_until(lambda: last_pt(s).startswith("#PTS50"), 1.5)
        assert s.follower.enabled  # слежение остаётся включённым — ждёт цель
    finally:
        s.stop()
