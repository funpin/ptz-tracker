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


# --- заряд ---

class BattBackend(FakeBackend):
    def __init__(self, script, values):
        super().__init__(script)
        self.values = list(values)
        self.reads = 0

    def battery(self):
        self.reads += 1
        v = self.values[0] if len(self.values) == 1 else self.values.pop(0)
        if isinstance(v, Exception):
            raise v
        return v


def mkb(values, script=()):
    b = BattBackend(script, values)
    clock = Clock()
    got, conn, batt = [], [], []
    th = GamepadThread(b, Mapper(lambda: False), got.append, conn.append, clock=clock,
                       on_battery=lambda p, c: batt.append((p, c)))
    return b, th, clock, batt


def test_battery_read_on_connect_and_every_5s_published_on_change():
    b, th, clock, batt = mkb([(70, False), (70, False), (60, False)])
    th.tick()
    assert batt == [(70, False)]
    clock.t = 4.0
    th.tick()
    assert b.reads == 1
    clock.t = 5.0
    th.tick()
    assert b.reads == 2 and batt == [(70, False)]  # без изменений не шлём
    clock.t = 10.0
    th.tick()
    assert batt == [(70, False), (60, False)]


def test_battery_failure_logged_once_and_publishes_none(caplog):
    b, th, clock, batt = mkb([RuntimeError("hid"), RuntimeError("hid")])
    th.tick()
    clock.t = 6.0
    th.tick()
    assert batt == [(None, None)]
    assert th._dead is False


def test_battery_reset_on_reconnect():
    b, th, clock, batt = mkb([(70, False)], script=[RawPad(), None, RawPad()])
    th.tick()
    th.tick()  # отключение
    assert batt[-1] == (None, None)
    clock.t = 0.1
    th.tick()  # снова: читаем сразу
    assert batt[-1] == (70, False) and b.reads == 2


def test_backend_without_battery_method_ok():
    b, th, got, conn = mk([RawPad()])
    th.tick()
    assert conn


class FakeHidLib:
    def __init__(self, reports, fail_open=False):
        self.reports = list(reports)
        self.opened, self.closed = [], 0
        self.fail_open = fail_open

    def SDL_hid_open(self, vid, pid, serial):
        self.opened.append((vid, pid, serial))
        return None if self.fail_open else 1234

    def SDL_hid_read_timeout(self, h, buf, n, ms):
        assert ms == 0
        if not self.reports:
            return 0
        r = self.reports.pop(0)
        for i, x in enumerate(r):
            buf[i] = x
        return len(r)

    def SDL_hid_close(self, h):
        self.closed += 1


def _rep(b, rid=0x31, idx=54):
    r = bytearray(78)
    r[0], r[idx] = rid, b
    return bytes(r)


def _backend(lib, name="DualSense Wireless Controller"):
    from ptztracker.gamepad.device import SdlBackend

    class Ctl:
        pass
    sb = SdlBackend()
    sb._ctl = Ctl()
    sb._ctl.name = name
    sb._sdl = lib
    sb._hid_lib = lambda: lib
    return sb


def test_sdl_battery_reads_latest_report():
    lib = FakeHidLib([_rep(0x05), _rep(0x07)])
    sb = _backend(lib)
    assert sb.battery() == (70, False)
    assert lib.opened == [(0x054C, 0x0CE6, None)]
    lib.reports = []
    assert sb.battery() == (70, False)  # нового отчёта нет: прежнее значение
    assert len(lib.opened) == 1
    sb._close_hid()
    assert lib.closed == 1


def test_sdl_battery_non_dualsense_and_failures():
    assert _backend(FakeHidLib([]), name="Xbox").battery() == (None, None)
    assert _backend(FakeHidLib([], fail_open=True)).battery() == (None, None)
    assert _backend(FakeHidLib([])).battery() == (None, None)  # отчётов ещё не было
    sb = _backend(None)
    sb._hid_lib = lambda: (_ for _ in ()).throw(OSError("нет SDL"))
    assert sb.battery() == (None, None)
