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
    sent = []
    for _ in range(3):
        sent.append(s.pump())
        clock.t += 1
    assert set(sent) == {"#PTS5050", "#Z50", "#F50"}
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


def test_busy_stop_command_never_dropped(env):
    # Final review C1: er2 (камера занята пультом) не должен приводить к потере «стопа»
    s, clock, send = env
    send.fail_next = [CameraError("er2", "eR2:PTS")] * 6
    s.submit("pt", "#PTS5050", Priority.MANUAL, only_if_changed=False)
    for _ in range(8):
        s.pump()
        clock.t += 0.131
    assert send.ok == ["#PTS5050"]


def test_emergency_never_dropped_even_on_er1(env):
    s, clock, send = env
    send.fail_next = [CameraError("er1", "eR1:Z")] * 5
    s.emergency_stop()
    for _ in range(12):
        s.pump()
        clock.t += 0.131
    assert set(send.ok) >= {"#PTS5050", "#Z50"}


def test_emergency_stop_also_stops_focus(env):
    s, clock, send = env
    s.emergency_stop()
    for _ in range(4):
        s.pump()
        clock.t += 0.131
    assert "#F50" in send.ok


def test_one_shot_ttl_expires_during_outage(env):
    # Final review I6: пресет, нажатый при обрыве, не должен сработать через минуту
    s, clock, send = env
    send.fail_next = [CameraUnreachable("x")] * 50
    s.submit("preset", "#R03", Priority.MANUAL, only_if_changed=False, ttl=2.0)
    for _ in range(30):
        s.pump()
        clock.t += 0.2
    send.fail_next = []
    for _ in range(5):
        s.pump()
        clock.t += 0.2
    assert "#R03" not in send.ok


def test_pan_tilt_before_zoom_when_tracking(env):
    # спека §7: #PTS важнее #Z — зум не должен задерживать поворот
    s, clock, _ = env
    s.submit("zoom", "#Z60", Priority.TRACKING_ZOOM)
    s.submit("pt", "#PTS6050", Priority.TRACKING)
    assert s.pump() == "#PTS6050"


def test_queries_only_drops_movement_and_stops():
    """Режим наблюдения (--lab record): камере уходят только запросы, ни движений, ни стопов."""
    clock, send = FakeClock(), Recorder()
    s = CommandScheduler(send, min_interval=0.130, clock=clock, queries_only=True)
    s.submit("pt", "#PTS6050", Priority.MANUAL)
    s.submit("zoom", "#Z60", Priority.TRACKING_ZOOM)
    s.emergency_stop()
    s.submit("heartbeat", "#O", Priority.QUERY, only_if_changed=False)
    assert s.pump() == "#O"
    clock.t += 1.0
    assert s.pump() is None
    assert send.calls == ["#O"]
    assert s.wait_emergency(0.1) is True
