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


class PowerJoy:
    def __init__(self, level):
        self.level = level

    def get_power_level(self):
        if isinstance(self.level, Exception):
            raise self.level
        return self.level


def _backend(level, name="DualSense Wireless Controller"):
    from ptztracker.gamepad.device import SdlBackend

    class Ctl:
        pass
    sb = SdlBackend()
    sb._ctl = Ctl()
    sb._ctl.name = name
    sb._ctl.as_joystick = lambda: PowerJoy(level)
    return sb


def test_battery_from_sdl_power_level():
    """Заряд — уровень SDL (грубо, верхняя граница), без своего HID-подключения к геймпаду."""
    assert _backend("full").battery() == (100, False)
    assert _backend("medium").battery() == (70, False)
    assert _backend("low").battery() == (20, False)
    assert _backend("empty").battery() == (5, False)
    assert _backend("wired").battery() == (None, True)
    assert _backend("unknown").battery() == (None, None)
    assert _backend("medium", name="Xbox Wireless Controller").battery() == (70, False)
    assert _backend(RuntimeError("sdl")).battery() == (None, None)


def test_backend_never_opens_hid_itself():
    """Своё HID-подключение поверх SDL на Mac гонялось с потоком чтения SDL и роняло программу."""
    import inspect

    import ptztracker.gamepad.device as dev
    assert "SDL_hid_" not in inspect.getsource(dev)


class FakeSerialLib:
    def __init__(self, serial):
        self.serial = serial

    def SDL_GameControllerFromInstanceID(self, iid):
        return 99 if iid == 7 else None

    def SDL_GameControllerGetSerial(self, ptr):
        assert ptr == 99
        return self.serial


def _id_backend(serial, guid="030000004c050000e60c000000000000", name="DualSense Wireless Controller"):
    from ptztracker.gamepad.device import SdlBackend

    class Joy:
        def get_instance_id(self):
            return 7

        def get_guid(self):
            return guid

        def get_power_level(self):
            return "full"

    class Ctl:
        pass
    sb = SdlBackend()
    sb._ctl = Ctl()
    sb._ctl.name = name
    sb._ctl.as_joystick = lambda: Joy()
    lib = FakeSerialLib(serial)
    sb._sdl_lib = lambda: lib
    return sb


def test_pad_id_is_serial_when_sdl_has_it():
    info = _id_backend(b"a0:b1:c2:d3:e4:f5").info()
    assert info["id"] == "a0:b1:c2:d3:e4:f5" and info["has_serial"] is True


def test_pad_id_falls_back_to_guid_and_name():
    for serial in (None, b""):
        info = _id_backend(serial).info()
        assert info["id"] == "030000004c050000e60c000000000000:DualSense Wireless Controller"
        assert info["has_serial"] is False


def test_pad_id_survives_sdl_failure():
    sb = _id_backend(None)
    sb._sdl_lib = lambda: (_ for _ in ()).throw(OSError("нет SDL"))
    info = sb.info()
    assert info["id"].endswith(":DualSense Wireless Controller") and info["has_serial"] is False


def test_no_controller_info_has_no_id():
    from ptztracker.gamepad.device import SdlBackend
    info = SdlBackend().info()
    assert info["id"] is None and info["has_serial"] is False


class TouchLib:
    """Подставная SDL: палец тачпада по сценарию; None — сбой вызова."""

    def __init__(self, fingers):
        self.fingers = list(fingers)

    def SDL_GameControllerFromInstanceID(self, iid):  # noqa: N802
        return 99

    def SDL_GameControllerGetTouchpadFinger(self, ptr, pad, finger, st, x, y, p):  # noqa: N802
        f = self.fingers.pop(0)
        if f is None:
            raise OSError("sdl")
        st._obj.value, x._obj.value, y._obj.value = (1, f[0], f[1]) if f else (0, 0.0, 0.0)
        return 0


def _touch_backend(fingers):
    from ptztracker.gamepad.device import SdlBackend

    class Joy:
        def get_instance_id(self):
            return 5

    class Ctl:
        name = "DualSense Wireless Controller"

        def as_joystick(self):
            return Joy()

    sb = SdlBackend()
    sb._ctl, sb._sdl = Ctl(), TouchLib(fingers)
    return sb


def test_touchpad_finger_read_into_rawpad():
    sb = _touch_backend([(0.25, 0.5), (), None, (0.75, 0.5)])
    assert sb._touch() == (0.25, 0.5)
    assert sb._touch() is None  # пальца нет
    assert sb._touch() is None  # сбой SDL не бросает
    assert sb._touch() == (0.75, 0.5)


def test_sdl_mfi_driver_disabled_before_sdl_starts():
    """MFi-драйвер SDL на Mac зовёт SDL из очереди GameController, пока поток опроса держит GIL и ждёт замок
    джойстиков SDL — программа зависала при подключении DualSense (2026-11-06). DualSense идёт через HIDAPI."""
    import os

    import ptztracker.gamepad.device  # noqa: F401
    assert os.environ.get("SDL_JOYSTICK_MFI") == "0"
