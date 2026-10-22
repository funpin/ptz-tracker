"""Follower кормит модель зума регулятора отправленными командами (быстро, без симулятора)."""

from ptztracker.camera.scheduler import CommandScheduler, Priority
from ptztracker.control.camera_model import default_calibration
from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.control.follower import Follower, sent_zoom_code


class FakeClock:
    t = 100.0

    def __call__(self):
        return self.t


def test_sent_zoom_code():
    assert sent_zoom_code({"key": "zoom", "cmd": "#Z74", "ok": True}) == 74
    assert sent_zoom_code({"key": "zoom", "cmd": "#Z74", "ok": False}) is None  # не дошла — камера не зумирует
    assert sent_zoom_code({"key": "pt", "cmd": "#PTS6050", "ok": True}) is None
    assert sent_zoom_code({"key": "zoom", "cmd": "#AXZ800", "ok": True}) is None


def test_follower_feeds_sent_zoom_into_controller():
    calib = default_calibration()
    clock = FakeClock()
    sched = CommandScheduler(lambda cmd: cmd.lstrip("#"), min_interval=0.130, clock=clock)
    f = Follower(None, sched, calib, lambda: 0.15, COMPOSITIONS["waist_center"], EffortProfile.at(0.5),
                 query_zoom=lambda: None)
    try:
        f.polling_paused = True  # такты слежения молчат: проверяем только путь «отправлено → модель»
        sched.submit("zoom", "#Z75", Priority.TRACKING_ZOOM)
        sched.pump()  # слежение выключено — модель не трогаем (её сбросит enable)
        assert f.controller.zdyn.velocity(1e9) == 0.0
        f.enabled = True
        sched.submit("zoom", "#Z99", Priority.TRACKING_ZOOM)
        clock.t += 0.2  # следующее окно
        sched.pump()
        assert abs(f.controller.zdyn.velocity(1e9) - calib.zoom.rate(99)) < 1e-9
    finally:
        f._stop.set()
