import threading
import time

from ptztracker.gamepad.device import GamepadThread, RawPad
from ptztracker.gamepad.intents import Disconnected
from ptztracker.gamepad.mapping import Mapper


class FakeBackend:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.threads = set()
        self.t = None

    def poll(self):
        self.threads.add(threading.get_ident())
        if not self.script:
            return RawPad()
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def info(self):
        return {"name": "fake"}

    def set_led(self, rgb):
        self.threads.add(threading.get_ident())
        self.calls.append(("led", rgb))

    def set_player_index(self, n):
        self.calls.append(("player", n))

    def rumble(self, low, high, ms):
        self.threads.add(threading.get_ident())
        self.calls.append(("rumble", low, high, ms))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def mk(script, clock=None):
    b = FakeBackend(script)
    got, conn = [], []
    th = GamepadThread(b, Mapper(lambda: False), got.append, conn.append, clock=clock or time.monotonic)
    return b, th, got, conn


def test_intents_delivered():
    b, th, got, conn = mk([RawPad(), RawPad(buttons=frozenset({"touchpad"}))])
    th.tick()
    th.tick()
    assert got
    assert conn == [{"name": "fake"}]


def test_unplug_sends_disconnected_once():
    b, th, got, conn = mk([RawPad(), None, None])
    for _ in range(3):
        th.tick()
    assert sum(x == [Disconnected()] for x in got) == 1
    assert conn == [{"name": "fake"}, None]


def test_exception_in_poll_sends_disconnected():
    b, th, got, conn = mk([RawPad(), RuntimeError("boom")])
    th.tick()
    th.tick()
    assert got[-1] == [Disconnected()]
    th.tick()  # мёртвый поток больше не опрашивает
    assert got[-1] == [Disconnected()]


def test_stop_sends_disconnected_and_neutral_led():
    b, th, got, conn = mk([RawPad()] * 1000)
    th.start()
    time.sleep(0.1)
    th.stop()
    assert got[-1] == [Disconnected()]
    assert b.calls[-1] == ("led", (0, 0, 0))


def test_led_rumble_run_on_poll_thread():
    b, th, got, conn = mk([RawPad()] * 1000)
    th.start()
    th.set_led((1, 2, 3))
    th.rumble(((0.5, 0.5, 20),))
    time.sleep(0.2)
    th.stop()
    assert ("led", (1, 2, 3)) in b.calls and ("rumble", 0.5, 0.5, 20) in b.calls
    assert b.threads == {th._thread.ident}


def test_reconnect_reports_connection_and_reapplies_led_player():
    b, th, got, conn = mk([RawPad(), None, RawPad()])
    th.set_led((9, 9, 9))
    th.set_player(2)
    for _ in range(3):
        th.tick()
    assert conn == [{"name": "fake"}, None, {"name": "fake"}]
    assert b.calls.count(("led", (9, 9, 9))) == 3 and b.calls.count(("player", 2)) == 3  # очередь + подключение + переподключение


def test_rumble_pattern_sequenced_without_blocking():
    c = Clock()
    b, th, got, conn = mk([RawPad()] * 20, c)
    th.tick()  # подключение
    th.rumble(((1.0, 0.0, 100), (0.0, 0.0, 100), (0.0, 1.0, 50)))
    for _ in range(30):
        th.tick()
        c.t += 0.02
    assert [x for x in b.calls if x[0] == "rumble"] == [("rumble", 1.0, 0.0, 100), ("rumble", 0.0, 1.0, 50)]


def test_disconnected_delivered_even_if_on_connection_raises():
    b, th, got, conn = mk([RawPad(), None])
    th.tick()

    def boom(info):
        raise RuntimeError("x")

    th._on_connection = boom
    th.tick()
    assert [Disconnected()] in got


def test_stop_on_stuck_thread_still_delivers_disconnected():
    gate = threading.Event()
    b, th, got, conn = mk([RawPad()])
    orig = b.poll

    def poll():
        if not b.script:
            gate.wait(5)
        return orig()

    b.poll = poll
    th.start()
    time.sleep(0.1)
    th.stop(timeout=0.1)
    assert got.count([Disconnected()]) == 1
    gate.set()
    time.sleep(0.2)
    assert got.count([Disconnected()]) == 1  # поздний shutdown не дублирует


def test_rumble_queued_while_disconnected_dropped():
    b, th, got, conn = mk([None, RawPad(), RawPad()])
    th.rumble(((1.0, 1.0, 50),))
    for _ in range(3):
        th.tick()
    assert not [c for c in b.calls if c[0] == "rumble"]


def test_on_intents_raises_on_unplug_still_reports_connection_lost():
    b, th, got, conn = mk([RawPad(), None])
    th.tick()
    calls = []

    def boom(x):
        calls.append(x)
        raise RuntimeError("x")

    th._on_intents = boom
    th.tick()
    assert conn[-1] is None and calls
