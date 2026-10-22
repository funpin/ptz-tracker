"""Follower кормит модель зума регулятора отправленными командами (быстро, без симулятора)."""

from types import SimpleNamespace

from ptztracker.camera.scheduler import CommandScheduler, Priority
from ptztracker.control.camera_model import default_calibration
from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.control.follower import Follower, sent_zoom_code


class FakeClock:
    t = 100.0

    def __call__(self):
        return self.t


class FakeAnalyzer:
    def latest(self):
        return SimpleNamespace(target_id=1)


def _follower(send=lambda cmd: cmd.lstrip("#")):
    """Follower без тактов слежения (потоки сразу остановлены): проверяем только путь «отправлено → модель»."""
    clock = FakeClock()
    sched = CommandScheduler(lambda cmd: send(cmd), min_interval=0.130, clock=clock)
    calib = default_calibration()
    f = Follower(FakeAnalyzer(), sched, calib, lambda: 0.15, COMPOSITIONS["waist_center"], EffortProfile.at(0.5),
                 query_zoom=lambda: None)
    f._stop.set()
    f._thread.join(1.0)
    f._zthread.join(1.0)
    return f, sched, clock, calib


def test_sent_zoom_code():
    assert sent_zoom_code({"key": "zoom", "cmd": "#Z74", "ok": True}) == 74
    assert sent_zoom_code({"key": "zoom", "cmd": "#Z74", "ok": False}) is None  # не дошла — камера не зумирует
    assert sent_zoom_code({"key": "pt", "cmd": "#PTS6050", "ok": True}) is None
    assert sent_zoom_code({"key": "zoom", "cmd": "#AXZ800", "ok": True}) is None


def test_follower_feeds_sent_zoom_into_controller():
    f, sched, clock, calib = _follower()
    sched.submit("zoom", "#Z75", Priority.MANUAL)
    sched.pump()  # слежение выключено — модель не трогаем (её сбросит enable)
    assert f.controller.zdyn.velocity(1e9) == 0.0
    assert f.enable()
    with f._lock:
        f._submit_tracking(50, 50, 99)
    for _ in range(2):  # стоп при выключении → затем зум слежения
        clock.t += 0.2
        sched.pump()
    assert abs(f.controller.zdyn.velocity(1e9) - calib.zoom.rate(99)) < 1e-9
    # момент команды в модели — начало отправки по часам планировщика (+ мёртвое время)
    assert f.controller.zdyn._cmds[-1][0] == clock.t + calib.dead_time


def test_stale_zoom_after_quick_disable_enable_ignored():
    """Команда зума ушла в камеру, пока слежение выключили и снова включили: новый регулятор её не учитывает."""
    holder = {}

    def send(cmd):
        if cmd == "#Z99":  # команда уже в пути, а оператор успел выключить и включить слежение
            holder["f"].disable("ручное управление")
            assert holder["f"].enable()
        return cmd.lstrip("#")

    f, sched, clock, calib = _follower(send)
    holder["f"] = f
    assert f.enable()
    with f._lock:
        f._submit_tracking(60, 50, 99)
    assert sched.pump() == "#PTS6050"
    clock.t += 0.2
    assert sched.pump() == "#Z99"
    assert f.enabled
    assert f.controller.zdyn.velocity(1e9) == 0.0
