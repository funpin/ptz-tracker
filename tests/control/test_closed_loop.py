"""Поведение «как оператор» в замкнутом контуре на симуляторе (реальное время, ~2,5 мин)."""

import time

import numpy as np
import pytest

from ptztracker.camera.session import CameraSession
from ptztracker.config import CameraConfig
from ptztracker.control.camera_model import default_calibration
from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.sim.scene import SyntheticStage

pytestmark = pytest.mark.slow


def wait_until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def run_follow(script, seconds, comp="waist_center", effort=0.5, sim_latency=None, calib_latency=None):
    cfg = CameraConfig(id="s", name="Сим", ip="sim", kind="sim", composition=comp, effort=effort)
    if calib_latency is not None:
        c = default_calibration()
        c.video_latency["sim"] = calib_latency
        cfg.calibration = c.to_json()
    s = CameraSession(cfg, scene=SyntheticStage(script=script), sim_latency=sim_latency)
    s.start()
    try:
        assert wait_until(lambda: s.analyzer.latest() is not None and len(s.analyzer.latest().tracks) > 0, 4)
        tr = s.analyzer.latest().tracks[0]
        s.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)
        assert wait_until(lambda: s.analyzer.latest().target_state == "tracking", 2)
        assert s.follower.enable()
        rows = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            fr = s.frames.latest()
            kp = fr.meta["people"][0]
            w, h = fr.image.shape[1], fr.image.shape[0]
            u = (kp[5, 1] + kp[6, 1]) / 2 / h - kp[0, 1] / h if kp[0, 2] > 0 and kp[5, 2] > 0 else None
            rows.append(
                dict(t=time.monotonic() - t0, x=kp[0, 0] / w, y=kp[0, 1] / h, vis=kp[0, 2] > 0, u=u,
                     pan=s.sim.pan_code, zoom=s.sim.zoom_code)
            )
            time.sleep(0.1)
        assert s.follower.enabled, f"слежение выключилось: {s.follower.last_reason}"
        return rows
    finally:
        s.stop()


def reversals(codes):
    signs = [1 if c > 50 else -1 for c in codes if c != 50]
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


def test_static_speaker_no_hunting():
    rows = run_follow("stand", 22)
    late = [r for r in rows if r["t"] > 10]
    assert reversals([r["pan"] for r in late]) <= 1
    assert all(r["pan"] == 50 for r in rows if r["t"] > 17)


def test_walk_and_stop_like_operator():
    rows = run_follow("lecture", 30)
    e = EffortProfile.at(0.5)
    ax = COMPOSITIONS["waist_center"].anchor[0]
    assert all(r["vis"] for r in rows), "человек выходил из кадра"
    warm = [r for r in rows if r["t"] > 6]
    errs = [abs(r["x"] - ax) for r in warm]
    # На старте ходьбы с крупного плана камера отстаёт (разгон ~0.85 с + задержка): главное — человек в кадре
    assert np.mean(errs) < 0.08 and max(errs) <= 0.40
    # Резкость — по ускорению (доли кадра/с²), а не по коду: шкала кодов сильно нелинейна,
    # а 60→40 через ноль — смена направления на малой скорости, не рывок.
    table = default_calibration().pan
    acc = [
        abs(table.speed(b["pan"], 0x73F) - table.speed(a["pan"], 0x73F)) / max(0.05, b["t"] - a["t"])
        for a, b in zip(rows, rows[1:])
    ]
    assert max(acc) <= 2.5 * e.a_max
    assert reversals([r["pan"] for r in rows]) <= 3


def test_soft_vs_sharp():
    soft = run_follow("lecture", 26, effort=0.0)
    sharp = run_follow("lecture", 26, effort=1.0)

    def max_speed(rows):
        return max(abs(r["pan"] - 50) for r in rows)

    def code_changes(rows):
        return sum(1 for a, b in zip(rows, rows[1:]) if a["pan"] != b["pan"])

    # «Мягко» — спокойнее: не быстрее и не суетливее, чем «Резко»
    assert max_speed(soft) <= max_speed(sharp)
    assert code_changes(soft) <= code_changes(sharp)
    assert all(r["vis"] for r in soft + sharp)


def test_zoom_converges_to_waist():
    rows = run_follow("stand", 20)
    us = [r["u"] for r in rows if r["t"] > 15 and r["u"] is not None]
    assert us and abs(np.mean(us) / COMPOSITIONS["waist_center"].u_frac - 1) < 0.15
    zsigns = [1 if r["zoom"] > 50 else -1 for r in rows if r["zoom"] != 50]
    assert sum(1 for a, b in zip(zsigns, zsigns[1:]) if a != b) <= 2


def test_with_h264_latency():
    rows = run_follow("lecture", 26, sim_latency=0.6, calib_latency=0.6)
    assert all(r["vis"] for r in rows), "человек выходил из кадра"
    assert reversals([r["pan"] for r in rows]) <= 3
