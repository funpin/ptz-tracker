import time

from ptztracker.camera.scheduler import Priority
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
        time.sleep(2.5)  # симулятор тормозит как HE42 (~0.8 с + хвост)
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


def test_stop_does_not_freeze_ui_on_dead_camera():
    # Final review I5: камера «висит» (TCP принимает, но не отвечает) — смена камеры/закрытие не подвешивают окно
    import socket

    hole = socket.socket()
    hole.bind(("127.0.0.1", 0))
    hole.listen(50)
    host = f"127.0.0.1:{hole.getsockname()[1]}"
    try:
        s = CameraSession(CameraConfig(id="x", name="X", ip=host))
        s.start()
        s.set_pan_tilt_velocity(1, 0)
        time.sleep(1.2)
        t0 = time.monotonic()
        s.stop()
        assert time.monotonic() - t0 < 0.7
    finally:
        hole.close()


def test_observe_only_session_sends_no_movement_or_stops():
    """Сессия наблюдения: ни ручные команды, ни аварийный стоп, ни stop() не шлют камере движений и стопов."""
    s = CameraSession(sim_cfg(), observe_only=True)
    s.start()
    try:
        s.set_pan_tilt_velocity(1, 0)
        s.set_zoom_velocity(1)
        s.emergency_stop()
        time.sleep(0.5)
    finally:
        s.stop()
    time.sleep(0.3)
    assert [c for c in list(s.sim.received) if c.startswith(("#PTS", "#Z", "#D1", "#AXF", "#F"))] == []


# --- питание и изображение ---


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_power_from_heartbeat():
    s = CameraSession(sim_cfg(), analysis=False)
    assert s.power == "unknown"
    s.start()
    try:
        assert _wait(lambda: s.power == "on")
    finally:
        s.stop()


def test_standby_roundtrip_blocks_motion():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        s.sim.power_on_s = 0.5
        assert _wait(lambda: s.power == "on")
        s.set_power(False)
        assert _wait(lambda: s.power == "standby", 1.0)
        n = len([c for c in s.sim.received if c.startswith("#PTS")])
        s.set_pan_tilt_velocity(0.5, 0)
        s.recall_preset(3)
        time.sleep(0.5)
        assert len([c for c in s.sim.received if c.startswith("#PTS")]) == n
        assert "#R03" not in s.sim.received
        s.set_power(True)
        assert _wait(lambda: s.power in ("switching", "on"), 1.0)
        assert _wait(lambda: s.power == "on", 4.0)
    finally:
        s.stop()


def test_power_off_runs_manual_first():
    s = CameraSession(sim_cfg(), analysis=False)
    calls = []
    s.on_manual = lambda: calls.append("manual")
    s.start()
    try:
        s.set_power(False)
        assert calls == ["manual"]
        assert _wait(lambda: "#O0" in s.sim.received, 1.0)
    finally:
        s.stop()


def test_image_state_read_and_set():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.gain == 0x08 and s.image.wb == 0 and s.image.color_temp == 0x1A)
        s.set_gain(0x0B)
        s.set_wb(9)
        s.set_color_temp(0x20)
        assert _wait(lambda: s.image.gain == 0x0B and s.image.wb == 9 and s.image.color_temp == 0x20, 1.5)
    finally:
        s.stop()


def test_rejected_gain_falls_back_to_camera_value():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.gain == 0x08)
        s.scheduler.submit("gain", "OGU:09", Priority.MANUAL)
        s.refresh_image()
        time.sleep(0.8)
        assert s.image.gain == 0x08
    finally:
        s.stop()


def test_unknown_power_reply_keeps_commands_flowing():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        orig = s.sim._dispatch
        s.sim._dispatch = lambda cmd: "ER1:O" if cmd == "#O" else orig(cmd)
        time.sleep(2.5)
        assert s.power == "unknown"
        s.recall_preset(4)
        assert _wait(lambda: "#R04" in s.sim.received, 1.0)
    finally:
        s.stop()
