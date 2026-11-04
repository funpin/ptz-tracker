import time

import pytest

from ptztracker.camera.session import CameraSession
from ptztracker.config import CameraConfig
from ptztracker.multicam.grid import CameraGrid


def sim_cfg(i):
    return CameraConfig(id=f"s{i}", name=f"Сим {i}", ip="sim", kind="sim")


def make(cfg, analysis):
    return CameraSession(cfg, analysis=analysis)


@pytest.fixture
def grid():
    g = CameraGrid(make)
    yield g
    g.close_all()


def pts(cell):
    return [c for c in list(cell.session.sim.received) if c.startswith("#PTS")]


def wait_for(cond, t=3.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def test_open_two_sims_independent(grid):
    a = grid.open(0, sim_cfg(0), False)
    b = grid.open(1, sim_cfg(1), False)
    assert a.session.sim is not b.session.sim
    a.mixer.set("pad", pan=1.0)
    assert wait_for(lambda: any(c != "#PTS5050" for c in pts(a)))
    time.sleep(0.3)
    assert pts(b) == []
    assert grid.ids() == ["s0", "s1", None, None]
    assert grid.slot_of("s1") == 1 and grid.slot_of("zz") is None


def test_same_camera_twice_refused(grid):
    grid.open(0, sim_cfg(0), False)
    with pytest.raises(ValueError):
        grid.open(1, sim_cfg(0), False)
    assert grid.cells[1] is None
    # та же камера в той же клетке — переоткрытие допустимо
    grid.open(0, sim_cfg(0), False)


def test_close_sends_stop(grid):
    c = grid.open(0, sim_cfg(0), False)
    sim = c.session.sim
    c.mixer.set("pad", pan=1.0)
    assert wait_for(lambda: any(x != "#PTS5050" for x in pts(c)))
    grid.close(0)
    assert grid.cells[0] is None
    assert [x for x in sim.received if x.startswith("#PTS")][-1] == "#PTS5050"


def test_emergency_stop_all(grid):
    a = grid.open(0, sim_cfg(0), False)
    b = grid.open(1, sim_cfg(1), False)
    for c in (a, b):
        c.gate.set_allowed(True)
        c.gate.set_off_air(True)
        c.mixer.set("pad", pan=1.0)
    assert wait_for(lambda: all(any(x != "#PTS5050" for x in pts(c)) for c in (a, b)))
    grid.emergency_stop_all()
    assert wait_for(lambda: all(pts(c) and pts(c)[-1] == "#PTS5050" for c in (a, b)))
    for c in (a, b):
        assert c.gate.control_allowed is False
        assert c.gate.not_on_air is False


def test_manual_revokes_only_own_gate(grid):
    a = grid.open(0, sim_cfg(0), False)
    b = grid.open(1, sim_cfg(1), False)
    for c in (a, b):
        c.gate.set_allowed(True)
        c.gate.set_off_air(True)
    a.session.set_pan_tilt_velocity(0.5, 0)
    assert a.gate.control_allowed is False
    assert b.gate.control_allowed is True and b.gate.not_on_air is True


def test_gate_callbacks(grid):
    c = grid.open(0, sim_cfg(0), False)
    calls = []
    c.gate.on_change = lambda: calls.append("change")
    c.gate.on_off_air_cleared = lambda: calls.append("off")
    c.gate.set_allowed(True)
    c.gate.set_off_air(True)
    c.gate.set_off_air(False)
    c.gate.revoke()
    assert "off" in calls and "change" in calls


def test_close_all_bounded():
    g = CameraGrid(make)
    bad = CameraConfig(id="bad", name="Нет", ip="10.255.255.1")
    g.open(0, bad, False)
    g.open(1, sim_cfg(1), False)
    t = time.monotonic()
    g.close_all(timeout=1.5)
    assert time.monotonic() - t <= 1.8
    assert g.cells == [None] * 4


class FakeSession:
    def __init__(self, cfg, fail_stop=False, fail_start=False):
        self.cfg, self.fail_stop, self.fail_start = cfg, fail_stop, fail_start
        self.on_manual = None
        self.calls = []

    def start(self):
        if self.fail_start:
            raise OSError("нет сети")

    def stop(self):
        self.calls.append("stop")

    def emergency_stop(self):
        self.calls.append("emergency_stop")
        if self.fail_stop:
            raise RuntimeError("сбой")

    def set_pan_tilt_velocity(self, vx, vy):
        pass

    def set_zoom_velocity(self, v):
        pass

    def set_focus_velocity(self, v):
        pass


def test_emergency_stop_all_survives_one_failure():
    g = CameraGrid(lambda cfg, a: FakeSession(cfg, fail_stop=cfg.id == "s0"))
    a, b = g.open(0, sim_cfg(0), False), g.open(1, sim_cfg(1), False)
    for c in (a, b):
        c.gate.set_allowed(True)
    g.emergency_stop_all()
    assert "emergency_stop" in b.session.calls
    assert a.gate.control_allowed is False and b.gate.control_allowed is False


def test_close_all_revokes_and_sends_stop_first():
    g = CameraGrid(lambda cfg, a: FakeSession(cfg))
    c = g.open(0, sim_cfg(0), False)
    c.gate.set_allowed(True)
    g.close_all()
    assert c.gate.control_allowed is False
    assert c.session.calls[0] == "emergency_stop"


def test_open_start_failure_cleans_up():
    sessions = []

    def make_(cfg, a):
        s = FakeSession(cfg, fail_start=True)
        sessions.append(s)
        return s

    g = CameraGrid(make_)
    with pytest.raises(OSError):
        g.open(0, sim_cfg(0), False)
    assert g.cells[0] is None
    assert sessions[0].calls == ["stop"]


def test_open_into_occupied_slot_closes_old():
    g = CameraGrid(lambda cfg, a: FakeSession(cfg))
    old = g.open(0, sim_cfg(0), False)
    g.open(0, sim_cfg(1), False)
    assert "stop" in old.session.calls
    assert g.ids() == ["s1", None, None, None]


def test_gate_revoke_wins_over_concurrent_set():
    import threading

    from ptztracker.multicam.agent import AgentGate

    cleared = []
    for _ in range(200):
        gate = AgentGate()
        gate.set_off_air(True)
        gate.on_off_air_cleared = lambda: cleared.append(1)
        ts = [threading.Thread(target=gate.revoke) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert gate.not_on_air is False
    assert len(cleared) == 200  # ровно один раз на каждый снятый флажок
