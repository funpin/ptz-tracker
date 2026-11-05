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
