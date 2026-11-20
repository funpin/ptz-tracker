"""Несколько геймпадов: индексы хаба и один поток опроса (подставной SDL, без устройства)."""

from ptztracker.gamepad.device import MultiPadThread, SdlHub


class Joy:
    def __init__(self, iid):
        self.iid = iid

    def get_instance_id(self):
        return self.iid


class Ctl:
    def __init__(self, iid):
        self.iid, self.up, self.quit_called = iid, True, False

    def attached(self):
        return self.up

    def as_joystick(self):
        return Joy(self.iid)

    def quit(self):
        self.quit_called = True


class FakeController:
    """Подставной pygame._sdl2.controller: .devices — instance id подключённых."""

    def __init__(self):
        self.devices = []
        self.opened = []

    def get_count(self):
        return len(self.devices)

    def is_controller(self, i):
        return True

    def Controller(self, i):  # noqa: N802
        c = Ctl(self.devices[i])
        self.opened.append(c)
        return c

    def quit(self):
        pass


class FakeEvent:
    pumped = 0

    def pump(self):
        FakeEvent.pumped += 1


class FakePg:
    event = FakeEvent()


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def mk_hub():
    clock, ctrl = Clock(), FakeController()
    hub = SdlHub(clock=clock)
    hub.pg, hub._controller = FakePg, ctrl
    return hub, ctrl, clock


def test_hub_assigns_first_free_index_and_keeps_it():
    hub, ctrl, clock = mk_hub()
    ctrl.devices = [10]
    hub.pump()
    assert hub.controller(0).iid == 10 and hub.controller(1) is None
    ctrl.devices = [10, 11]
    clock.t = 1.0
    hub.pump()
    assert [hub.controller(i).iid if hub.controller(i) else None for i in range(3)] == [10, 11, None]
    hub.controller(0).up = False  # первый вынули: второй остаётся на своём индексе
    ctrl.devices = [11]
    hub.pump()
    assert hub.controller(0) is None and hub.controller(1).iid == 11
    ctrl.devices = [11, 12]  # новый занимает освободившийся индекс 0
    clock.t = 2.0
    hub.pump()
    assert hub.controller(0).iid == 12 and hub.controller(1).iid == 11


def test_hub_scans_at_most_twice_a_second_and_holds_four():
    hub, ctrl, clock = mk_hub()
    ctrl.devices = [1]
    hub.pump()
    ctrl.devices = [1, 2]
    clock.t = 0.2
    hub.pump()
    assert hub.controller(1) is None  # рано
    clock.t = 0.6
    ctrl.devices = [1, 2, 3, 4, 5]
    hub.pump()
    assert [hub.controller(i) is not None for i in range(4)] == [True] * 4
    assert sorted(hub.controller(i).iid for i in range(4)) == [1, 2, 3, 4]


class T:
    def __init__(self, name, log):
        self.name, self.log = name, log

    def tick(self):
        self.log.append(self.name)

    def shutdown(self):
        self.log.append(self.name + ".shutdown")

    def _send_disconnected(self):
        pass


class H:
    def __init__(self, log):
        self.log = log

    def open(self):
        return True

    def pump(self):
        self.log.append("pump")

    def close(self):
        self.log.append("close")


def test_multipad_tick_order_and_stop_closes_hub():
    log = []
    m = MultiPadThread(H(log), [T("a", log), T("b", log)])
    m.tick()
    assert log == ["pump", "a", "b"]
    log.clear()
    m.stop()  # не запускали: сразу shutdown
    assert log == ["a.shutdown", "b.shutdown", "close"]


def test_multipad_thread_runs_and_stops():
    log = []
    m = MultiPadThread(H(log), [T("a", log)], hz=1000.0)
    m.start()
    import time
    time.sleep(0.05)
    m.stop(1.0)
    assert log[:2] == ["pump", "a"] and log[-2:] == ["a.shutdown", "close"]


class AxisCtl(Ctl):
    def get_axis(self, _a):
        return 32767

    def get_button(self, _b):
        return False


class AxisPg(FakePg):
    CONTROLLER_AXIS_LEFTX = 0
    CONTROLLER_AXIS_TRIGGERLEFT = 4
    CONTROLLER_AXIS_TRIGGERRIGHT = 5

    class error(Exception):  # noqa: N801
        pass


def test_hub_backend_view_polls_real_controller():
    """Вид хаба — настоящий SdlBackend: без собственного open() он бы молчал (так было в 57e58b3)."""
    hub, ctrl, _ = mk_hub()
    hub.pg, hub.mapping = AxisPg, {"cross": 0}
    view = hub.backend(1)
    hub._slots[1] = AxisCtl(42)
    pad = view.poll()
    assert pad is not None and pad.axes["leftx"] == 1.0


class OpenHub(H):
    """Хаб, который помнит, из какого потока его открыли."""

    def __init__(self, log, ok=True):
        super().__init__(log)
        self.ok, self.open_thread = ok, None

    def open(self):
        import threading
        self.open_thread = threading.current_thread().name
        self.log.append("open")
        return self.ok


def test_multipad_thread_opens_sdl_on_its_own_thread():
    """На Mac SDL доставляет события HID в поток, где его запустили: запуск в потоке Qt ронял программу."""
    log = []
    hub = OpenHub(log)
    m = MultiPadThread(hub, [T("a", log)], hz=1000.0)
    assert hub.open_thread is None  # конструктор SDL не трогает
    m.start()
    import time
    time.sleep(0.05)
    m.stop(1.0)
    assert hub.open_thread == "gamepads"
    assert log[:3] == ["open", "pump", "a"]


def test_multipad_thread_ends_quietly_when_sdl_fails():
    log = []
    m = MultiPadThread(OpenHub(log, ok=False), [T("a", log)], hz=1000.0)
    m.start()
    m._thread.join(1.0)
    assert not m._thread.is_alive()
    assert "pump" not in log and log[-2:] == ["a.shutdown", "close"]


def test_hub_backend_view_does_not_start_sdl():
    """Вид создаётся в потоке Qt: SDL ему трогать нельзя, pygame он берёт у хаба при первом опросе."""
    hub = SdlHub()
    hub.open = lambda: (_ for _ in ()).throw(AssertionError("SDL запущен из конструктора вида"))
    view = hub.backend(0)
    assert view.poll() is None  # хаб ещё не открыт: геймпадов нет


class CachingController(FakeController):
    """Как настоящий pygame-ce: Controller(i) для уже открытого устройства возвращает тот же объект."""

    def __init__(self):
        super().__init__()
        self.cache = {}

    def Controller(self, i):  # noqa: N802
        iid = self.devices[i]
        if iid not in self.cache or self.cache[iid].quit_called:
            self.cache[iid] = Ctl(iid)
        return self.cache[iid]


def test_hub_rescan_does_not_close_the_pad_it_already_holds():
    """Повторный поиск находил тот же объект и закрывал его как «дубль»: геймпад пропадал каждые 0,5 с."""
    hub, _, clock = mk_hub()
    hub._controller = ctrl = CachingController()
    ctrl.devices = [7]
    hub.pump()
    first = hub.controller(0)
    for k in range(1, 6):
        clock.t = k * 0.6
        hub.pump()
    assert hub.controller(0) is first and not first.quit_called
    assert hub.controller(1) is None


class PickyController(FakeController):
    """Часть устройств SDL не считает геймпадом (нет раскладки), одно не открывается."""

    def __init__(self, not_pads=(), broken=()):
        super().__init__()
        self.not_pads, self.broken = set(not_pads), set(broken)

    def is_controller(self, i):
        return self.devices[i] not in self.not_pads

    def Controller(self, i):  # noqa: N802
        if self.devices[i] in self.broken:
            raise RuntimeError("Couldn't open joystick")
        return super().Controller(i)


def _seen_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Геймпады: SDL")]


def test_hub_logs_what_sdl_sees_and_why_a_device_is_skipped(caplog):
    """На Windows геймпад «не виден», а в журнале пусто: пишем каждое устройство SDL и что с ним стало."""
    caplog.set_level("INFO", logger="ptztracker.gamepad.device")
    hub, _, _ = mk_hub()
    hub._controller = ctrl = PickyController(not_pads={2}, broken={3})
    hub._describe = lambda i: f"«Устройство {ctrl.devices[i]}» 054c:0ce6"
    ctrl.devices = [1, 2, 3]
    hub.pump()
    (line,) = _seen_lines(caplog)
    assert "3 устр" in line
    assert "«Устройство 1» 054c:0ce6 — геймпад" in line
    assert "«Устройство 2» 054c:0ce6 — пропущено: SDL не знает раскладку кнопок" in line
    assert "«Устройство 3» 054c:0ce6 — не открылось: RuntimeError: Couldn't open joystick" in line
    assert hub.controller(0).iid == 1 and hub.controller(1) is None


def test_hub_logs_when_sdl_sees_nothing_and_only_on_change(caplog):
    caplog.set_level("INFO", logger="ptztracker.gamepad.device")
    hub, ctrl, clock = mk_hub()
    hub._describe = lambda i: "«DualSense»"
    for k in range(5):  # пусто: одна строка, а не каждые 0,5 с
        clock.t = k * 0.6
        hub.pump()
    assert _seen_lines(caplog) == ["Геймпады: SDL не видит ни одного устройства ввода"]
    ctrl.devices = [7]
    clock.t = 5.0
    hub.pump()
    clock.t = 6.0
    hub.pump()
    assert len(_seen_lines(caplog)) == 2 and "«DualSense» — геймпад" in _seen_lines(caplog)[1]


def test_hub_describe_survives_missing_sdl_library(monkeypatch):
    from ptztracker.gamepad import device
    monkeypatch.setattr(device, "load_sdl_lib", lambda: (_ for _ in ()).throw(OSError("нет SDL")))
    assert SdlHub()._describe(0) == "?"


class ErrPg(FakePg):
    @staticmethod
    def get_error():
        return "HIDAPI read failed"


def test_hub_logs_why_sdl_dropped_a_pad(caplog):
    """Windows, 1.5.0: «подключён» и сразу «отключён», в журнале ни слова почему. Пишем ошибку SDL."""
    caplog.set_level("INFO", logger="ptztracker.gamepad.device")
    hub, ctrl, clock = mk_hub()
    hub.pg = ErrPg
    hub._describe = lambda i: "«DualSense»"
    ctrl.devices = [5]
    hub.pump()
    ctl = hub.controller(0)
    ctl.name = "DualSense Wireless Controller"
    ctl.up = False
    ctrl.devices = []
    hub.pump()
    lines = [r.getMessage() for r in caplog.records if "отключ" in r.getMessage()]
    assert lines == ["Геймпады: SDL отключила «DualSense Wireless Controller» (номер 0); ошибка SDL: HIDAPI read failed"]
    assert hub.controller(0) is None


def test_backend_logs_sdl_error_while_polling_at_info(caplog):

    class Boom(Exception):
        pass

    class Pg:
        error = Boom
        CONTROLLER_AXIS_LEFTX, CONTROLLER_AXIS_TRIGGERLEFT, CONTROLLER_AXIS_TRIGGERRIGHT = 0, 4, 5

    class BadCtl:
        def get_axis(self, a):
            raise Boom("Controller is not initialized")

    caplog.set_level("INFO", logger="ptztracker.gamepad.device")
    hub = SdlHub()
    hub.pg, hub._slots[0] = Pg, BadCtl()
    b = hub.backend(0)
    assert b.poll() is None and b.poll() is None
    lines = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert lines == ["Геймпады: опрос номера 0 не удался: Controller is not initialized"]
