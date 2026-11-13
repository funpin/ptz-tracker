"""Область сцены в сессии камеры: отметка «на сцене» у людей в анализе (симулятор, реальное время)."""

import time

from ptztracker.camera.session import CameraSession
from ptztracker.config import CameraConfig
from ptztracker.control.stage_area import StageArea
from ptztracker.recorder.events import track_to_json


def wait_until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _tracks(s):
    r = s.analyzer.latest()
    return [] if r is None else [t for t in r.tracks if t.last_seen == r.t_capture]


def test_people_are_marked_on_stage_or_not():
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"))
    s.start()
    try:
        assert wait_until(lambda: len(_tracks(s)) > 0, 4)
        assert all(t.on_stage for t in _tracks(s))  # области нет — все «на сцене»
        s.set_stage_area(StageArea(-180.0, 180.0, 60.0, 90.0))  # область — только под потолком
        assert wait_until(lambda: _tracks(s) and not any(t.on_stage for t in _tracks(s)), 6)
        assert track_to_json(_tracks(s)[0])["stage"] is False
        s.set_stage_area(StageArea(-180.0, 180.0, 60.0, 90.0, on=False))  # выключена — снова все
        assert wait_until(lambda: _tracks(s) and all(t.on_stage for t in _tracks(s)), 4)
        assert s.cfg.stage_area == {"pan": [-180.0, 180.0], "tilt": [60.0, 90.0], "on": False}
    finally:
        s.stop()


def test_area_from_config_is_used_and_setting_it_sends_nothing_to_camera():
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim",
                                   stage_area={"pan": [-180.0, 180.0], "tilt": [60.0, 90.0], "on": True}))
    s.start()
    try:
        assert wait_until(lambda: _tracks(s) and not any(t.on_stage for t in _tracks(s)), 8)
        n = len([c for c in s.sim.received if c.startswith(("#PTS", "#Z", "#APS", "#AXZ"))])
        s.set_stage_area(None)
        s.set_stage_area(StageArea(-10.0, 10.0, -5.0, 5.0))
        time.sleep(0.5)
        assert len([c for c in s.sim.received if c.startswith(("#PTS", "#Z", "#APS", "#AXZ"))]) == n
    finally:
        s.stop()


def test_recording_meta_carries_current_area():
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"))
    assert s._live_meta()["stage_area"] is None
    s.set_stage_area(StageArea(-10.0, 10.0, -5.0, 5.0))
    assert s._live_meta()["stage_area"] == {"pan": [-10.0, 10.0], "tilt": [-5.0, 5.0], "on": True}


def _answers(s, pan_units, zoom=0x555):
    """Камера ответила на #GZ и #APC (как из потока планировщика)."""
    from ptztracker.camera import protocol as P

    s._on_ptz_reply(P.Q_ZOOM, f"gz{zoom:03X}")
    s._on_ptz_reply(P.Q_PAN_TILT, f"aPC{pan_units:04X}8000")


def test_view_is_unknown_while_camera_moves_without_following():
    """Пресет или чужой пульт: два ответа о положении расходятся — камера едет, вид неизвестен, а не «как был»."""
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"))
    _answers(s, 0x8000)
    assert s.stage_view(time.monotonic()) is None  # один ответ: стоит ли камера, ещё не знаем
    _answers(s, 0x8000)
    v = s.stage_view(time.monotonic())
    assert v is not None and v.pan_deg == 0.0
    _answers(s, 0x8200)  # поехала
    assert s.stage_view(time.monotonic()) is None
    _answers(s, 0x8400)
    assert s.stage_view(time.monotonic()) is None
    _answers(s, 0x8400)  # встала
    assert s.stage_view(time.monotonic()).pan_deg > 8
    _answers(s, 0x8400, zoom=0x800)  # едет зум
    assert s.stage_view(time.monotonic()) is None


def test_view_is_unknown_for_a_frame_much_older_or_newer_than_the_answer():
    from ptztracker.camera.session import VIEW_MAX_AGE_S

    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"))
    _answers(s, 0x8000)
    _answers(s, 0x8000)
    now = time.monotonic()
    assert s.stage_view(now) is not None
    assert s.stage_view(now + VIEW_MAX_AGE_S + 1) is None
    assert s.stage_view(now - VIEW_MAX_AGE_S - 1) is None  # видео зависло на старом кадре
    assert s.stage_view(0.0) is None  # кадра ещё не было


def test_unknown_view_marks_nobody(monkeypatch):
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim",
                                   stage_area={"pan": [-180.0, 180.0], "tilt": [60.0, 90.0], "on": True}))
    assert s._stage_flags([object()], time.monotonic(), (1280, 720)) is None  # вида нет — область не действует
