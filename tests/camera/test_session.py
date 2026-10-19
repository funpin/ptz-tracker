import time

from ptztracker.camera.session import CameraSession
from ptztracker.config import CameraConfig


def sim_cfg():
    return CameraConfig(id="s", name="Сим", ip="sim", kind="sim")


def test_sim_session_manual_pan():
    s = CameraSession(sim_cfg())
    s.start()
    try:
        s.set_pan_tilt_velocity(1, 0)
        time.sleep(0.6)
        s.set_pan_tilt_velocity(0, 0)
        time.sleep(0.6)
        assert s.sim.pan_deg > 5
        p = s.sim.pan_deg
        time.sleep(0.3)
        assert abs(s.sim.pan_deg - p) < 0.2
    finally:
        s.stop()


def test_sim_session_has_video_and_online():
    s = CameraSession(sim_cfg())
    s.start()
    try:
        time.sleep(0.8)
        assert s.frames.latest() is not None
        assert s.online is True
    finally:
        s.stop()


def test_zoom_and_presets_reach_camera():
    s = CameraSession(sim_cfg())
    s.start()
    try:
        s.set_zoom_velocity(1.0)
        time.sleep(0.4)
        s.set_zoom_velocity(0)
        s.store_preset(2)
        s.set_iris(1.0)
        s.set_focus_auto(False)
        time.sleep(0.8)
        rec = list(s.sim.received)
        assert "#Z99" in rec and "#Z50" in rec and "#M02" in rec
        assert "#AXIFFF" in rec and "#D10" in rec
    finally:
        s.stop()


def test_stop_sends_emergency_stop():
    s = CameraSession(sim_cfg())
    s.start()
    s.set_pan_tilt_velocity(1, 0)
    time.sleep(0.3)
    s.stop()
    pts = [c for c in s.sim.received if c.startswith("#PTS")]
    assert pts[-1] == "#PTS5050"
    assert "#Z50" in s.sim.received


def test_unreachable_camera_session_does_not_block():
    s = CameraSession(CameraConfig(id="x", name="X", ip="10.255.255.1"))
    t0 = time.monotonic()
    s.start()
    s.set_pan_tilt_velocity(1, 0)
    assert time.monotonic() - t0 < 0.5
    time.sleep(1.5)
    assert s.online is False
    t0 = time.monotonic()
    s.stop()
    assert time.monotonic() - t0 < 3.5
