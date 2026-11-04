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
