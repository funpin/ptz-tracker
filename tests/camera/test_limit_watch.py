import time

import pytest

from ptztracker.camera import protocol as P
from ptztracker.camera.limit_watch import LimitWatch
from ptztracker.camera.session import CameraSession
from ptztracker.config import CameraConfig


def make(pt=lambda: (2000, 2000), z=lambda: 2000, **kw):
    calls = {"pt": 0, "z": 0, "limit": 0}

    def qpt():
        calls["pt"] += 1
        return pt()

    def qz():
        calls["z"] += 1
        return z()

    def lim():
        calls["limit"] += 1

    kw = {"period_s": 0.02, "stuck_s": 0.08, "pulse_s": 0.1, **kw}
    return LimitWatch(qpt, qz, lim, **kw), calls


def test_pulses_while_stuck():
    w, c = make()
    try:
        w.set_demand(1, 0, 0)
        time.sleep(0.6)
        assert c["limit"] >= 2
    finally:
        w.stop()


def test_no_pulse_while_moving():
    pos = [2000]

    def pt():
        pos[0] += 20
        return pos[0], 2000

    w, c = make(pt=pt)
    try:
        w.set_demand(1, 0, 0)
        time.sleep(0.4)
        assert c["limit"] == 0
    finally:
        w.stop()


def test_no_queries_without_demand():
    w, c = make()
    try:
        time.sleep(0.2)
        w.set_demand(0, 0, 0)
        time.sleep(0.2)
        assert c["pt"] == 0 and c["z"] == 0
    finally:
        w.stop()


def test_zoom_edge_immediate():
    w, c = make(z=lambda: P.LENS_MAX, stuck_s=5.0)
    try:
        w.set_demand(0, 0, 1)
        time.sleep(0.15)
        assert c["limit"] >= 1
        assert c["pt"] == 0
    finally:
        w.stop()


def test_on_limit_exception_does_not_kill_loop():
    n = [0]

    def lim():
        n[0] += 1
        raise RuntimeError("x")

    w = LimitWatch(lambda: (1, 1), lambda: 2000, lim, period_s=0.02, stuck_s=0.05, pulse_s=0.05)
    try:
        w.set_demand(1, 0, 0)
        time.sleep(0.4)
        assert n[0] >= 2
    finally:
        w.stop()


@pytest.mark.slow
def test_sim_pan_limit():
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"))
    s.start()
    hits = []
    try:
        s.limit_watch.set_on_limit(lambda: hits.append(time.monotonic()))
        s.set_pan_tilt_velocity(1, 0)
        deadline = time.monotonic() + 40
        while s.sim.pan_deg < 174 and time.monotonic() < deadline:
            time.sleep(0.2)
        assert s.sim.pan_deg >= 174
        hits.clear()
        s.limit_watch.set_demand(1, 0, 0)
        time.sleep(2.0)
        assert len(hits) >= 2
    finally:
        s.limit_watch.set_demand(0, 0, 0)
        s.stop()


def test_deadzone_demand_is_zero():
    w, c = make()
    try:
        w.set_demand(0.01, -0.01, 0.0)
        time.sleep(0.3)
        assert c["pt"] == 0 and c["limit"] == 0
    finally:
        w.stop()


def test_slow_demand_not_judged():
    w, c = make()
    try:
        w.set_demand(0.05, 0, 0)
        time.sleep(0.4)
        assert c["limit"] == 0
    finally:
        w.stop()


def test_startup_grace():
    w, c = make(stuck_s=0.3, period_s=0.02)
    try:
        w.set_demand(1, 0, 0)
        time.sleep(0.25)
        assert c["limit"] == 0
        time.sleep(0.4)
        assert c["limit"] >= 1
    finally:
        w.stop()


def test_sign_change_resets_grace():
    w, c = make(stuck_s=0.3, period_s=0.02)
    try:
        w.set_demand(1, 0, 0)
        time.sleep(0.5)
        assert c["limit"] >= 1
        n = c["limit"]
        w.set_demand(-1, 0, 0)
        time.sleep(0.2)
        assert c["limit"] == n
    finally:
        w.stop()


def test_diagonal_slow_axis_regrace():
    w, c = make(stuck_s=0.3, period_s=0.02)
    try:
        w.set_demand(1, 1, 0)
        time.sleep(0.6)
        assert c["limit"] >= 1
        w.set_demand(0.05, 1, 0)  # pan замедлился ниже порога
        time.sleep(0.1)
        n = c["limit"]
        w.set_demand(1, 0.05, 0)  # вернулся на полную скорость: льготный период заново
        time.sleep(0.2)
        assert c["limit"] == n
    finally:
        w.stop()


def test_set_on_limit_replaces_callback():
    w, c = make()
    hits = []
    try:
        w.set_on_limit(lambda: hits.append(1))
        w.set_demand(1, 0, 0)
        time.sleep(0.4)
        assert hits and c["limit"] == 0
    finally:
        w.stop()
