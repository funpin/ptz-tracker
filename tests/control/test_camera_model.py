import math

from ptztracker.config import ConfigStore, default_config
from ptztracker.control.camera_model import (
    Calibration,
    CameraDynamics,
    SpeedTable,
    ZoomTable,
    default_calibration,
)

Z = 0x73F


def test_speed_interpolates_and_symmetric():
    t = default_calibration().pan
    assert abs(t.speed(60, Z) - 0.034) < 1e-9 and abs(t.speed(40, Z) + 0.034) < 1e-9
    assert t.speed(50, Z) == 0.0
    mid = t.speed(61, Z)
    assert 0.034 < mid < 0.07


def test_code_for_minimal_and_deadband():
    t = default_calibration().pan
    assert t.code_for(0.0005, Z) == 50
    assert t.code_for(0.034, Z) == 60
    assert t.code_for(-0.33, Z) == 30
    assert t.code_for(5.0, Z) == 99


def test_code_for_hysteresis():
    t = default_calibration().pan
    want = t.speed(64, Z) * 1.08
    assert t.code_for(want, Z, current=64) == 64
    assert t.code_for(want, Z, current=50) == 65


def test_zoom_interpolation_between_positions():
    t = SpeedTable({0x555: [(60, 0.1), (70, 0.4)], 0xFFF: [(60, 0.02), (70, 0.08)]})
    mid = (0x555 + 0xFFF) // 2
    assert abs(t.speed(60, mid) - 0.06) < 0.002
    assert abs(t.speed(60, 0x400) - 0.1) < 1e-9  # за краем — крайняя таблица


def test_zoom_table():
    z = default_calibration().zoom
    assert z.rate(50) == 0.0 and z.rate(75) > 0 and z.rate(25) < 0
    assert z.code_for(0.0) == 50 and z.code_for(-0.27) == 25


def test_dynamics_displacement_step():
    d = CameraDynamics(dead_time=0.08, tau=0.37)
    d.command(0.0, 0.3)
    assert d.displacement(0.0, 0.08) == 0.0
    assert d.velocity(2.0) > 0.29
    # аналитика первого порядка: x(t) = v·(s − τ(1 − e^(−s/τ))), s = t − dead
    s = 1.0 - 0.08
    assert abs(d.displacement(0.0, 1.0) - 0.3 * (s - 0.37 * (1 - math.exp(-s / 0.37)))) < 1e-6


def test_dynamics_stop_and_reverse():
    d = CameraDynamics(dead_time=0.0, tau=0.37)
    d.command(0.0, 0.3)
    d.command(1.0, 0.0)
    assert d.velocity(1.0) > 0.2 and d.velocity(4.0) < 0.01
    d.command(5.0, -0.2)
    assert d.velocity(8.0) < -0.19


def test_calibration_json_roundtrip_and_config(tmp_path):
    c = default_calibration()
    c2 = Calibration.from_json(c.to_json())
    assert c2.pan.speed(70, Z) == c.pan.speed(70, Z) and c2.tau == c.tau and c2.video_latency == c.video_latency
    store = ConfigStore(tmp_path / "c.json")
    cfg = default_config()
    cfg.cameras[0].calibration = c.to_json()
    store.save(cfg)
    loaded = store.load().cameras[0].calibration
    assert Calibration.from_json(loaded).zoom.rate(75) == c.zoom.rate(75)
