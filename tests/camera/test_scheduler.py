import threading

import pytest

from ptztracker.camera.client import CameraUnreachable
from ptztracker.camera.protocol import CameraError
from ptztracker.camera.scheduler import CommandScheduler, Priority


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Recorder:
    """send(): возвращает эхо; можно заранее задать ошибки на ближайшие вызовы."""

    def __init__(self):
        self.calls: list[str] = []
        self.ok: list[str] = []
        self.fail_next: list[Exception] = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if self.fail_next:
            raise self.fail_next.pop(0)
        self.ok.append(cmd)
        return "p1" if cmd == "#O" else cmd.lstrip("#")


@pytest.fixture
def env():
    clock, send = FakeClock(), Recorder()
    return CommandScheduler(send, min_interval=0.130, clock=clock), clock, send


def test_respects_min_interval(env):
    s, clock, _ = env
    s.submit("pt", "#PTS6050", Priority.MANUAL)
    assert s.pump() == "#PTS6050"
    s.submit("zoom", "#Z60", Priority.MANUAL)
    clock.t += 0.10
    assert s.pump() is None
    clock.t += 0.031
    assert s.pump() == "#Z60"


def test_coalesces_same_key(env):
    s, clock, send = env
    s.submit("pt", "#PTS6050", Priority.TRACKING)
    s.submit("pt", "#PTS7050", Priority.TRACKING)
    assert s.pump() == "#PTS7050"
    clock.t += 1
    assert s.pump() is None
    assert send.calls == ["#PTS7050"]


def test_only_if_changed(env):
    s, clock, _ = env
    s.submit("pt", "#PTS6050", Priority.TRACKING)
    s.pump()
    clock.t += 1
    s.submit("pt", "#PTS6050", Priority.TRACKING)
    assert s.pump() is None


def test_return_to_last_sent_cancels_pending(env):
    s, clock, send = env
    s.submit("pt", "#PTS5050", Priority.MANUAL)
    s.pump()
    clock.t += 0.01
    s.submit("pt", "#PTS6050", Priority.MANUAL)  # ещё не отправлено (интервал)
    s.submit("pt", "#PTS5050", Priority.MANUAL)  # вернулись к уже отправленному
    clock.t += 1
    assert s.pump() is None
    assert send.calls == ["#PTS5050"]


def test_priority_order(env):
    s, _, _ = env
    s.submit("zoom", "#Z60", Priority.TRACKING)
    s.submit("pt", "#PTS4050", Priority.MANUAL)
    assert s.pump() == "#PTS4050"


def test_emergency_stop_preempts_and_clears(env):
    s, clock, _ = env
    s.submit("pt", "#PTS9950", Priority.MANUAL)
    s.emergency_stop()
    first = s.pump()
    clock.t += 1
    second = s.pump()
    clock.t += 1
    assert {first, second} == {"#PTS5050", "#Z50"}
    assert s.pump() is None


def test_camera_error_retried_not_lost(env):
    s, clock, send = env
    send.fail_next = [CameraError("er2", "eR2:PTS")]
    s.submit("pt", "#PTS6050", Priority.TRACKING)
    s.pump()
    clock.t += 0.131
    assert s.pump() == "#PTS6050"
    assert send.ok == ["#PTS6050"]


def test_persistent_camera_error_dropped_after_3(env):
    s, clock, send = env
    send.fail_next = [CameraError("er1", "eR1:X")] * 3
    s.submit("x", "#X", Priority.MANUAL)
    for _ in range(5):
        s.pump()
        clock.t += 0.2
    assert send.calls == ["#X"] * 3


def test_unreachable_sets_offline_and_recovers(env):
    s, clock, send = env
    changes = []
    s.on_state_change = changes.append
    send.fail_next = [CameraUnreachable("x")]
    s.submit("pt", "#PTS6050", Priority.MANUAL)
    s.pump()
    assert s.online is False
    clock.t += 0.131
    s.pump()
    assert s.online is True
    assert changes == [False, True]


def test_backoff_after_three_unreachable(env):
    s, clock, send = env
    send.fail_next = [CameraUnreachable("x")] * 3
    s.submit("pt", "#PTS6050", Priority.MANUAL)
    for _ in range(3):
        s.pump()
        clock.t += 0.131
    assert s.pump() is None  # пауза 1 с
    clock.t += 1.0
    assert s.pump() == "#PTS6050"


def test_query_returns_response_threaded():
    send = Recorder()
    s = CommandScheduler(send, min_interval=0.01)
    s.start()
    try:
        assert s.query("#O") == "p1"
    finally:
        s.stop()


def test_query_error_propagates():
    send = Recorder()
    send.fail_next = [CameraError("er1", "eR1:Q")]
    s = CommandScheduler(send, min_interval=0.01)
    s.start()
    try:
        with pytest.raises(CameraError):
            s.query("#Q")
    finally:
        s.stop()


def test_submit_not_blocked_by_slow_send():
    gate = threading.Event()

    def slow_send(cmd):
        gate.wait(2)
        return "ok"

    s = CommandScheduler(slow_send, min_interval=0.01)
    s.start()
    try:
        s.submit("pt", "#PTS6050", Priority.MANUAL)
        import time

        time.sleep(0.05)
        t0 = time.monotonic()
        s.submit("pt", "#PTS7050", Priority.MANUAL)
        assert time.monotonic() - t0 < 0.05
    finally:
        gate.set()
        s.stop()
