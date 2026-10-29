import math

from ptztracker.config import ConfigStore, default_config
from ptztracker.control.camera_model import (
    Calibration,
    CameraDynamics,
    SpeedTable,
    default_calibration,
)

Z = 0x73F


def test_speed_interpolates_and_symmetric():
    t = default_calibration().pan
    v60, v62 = t.speed(60, Z), t.speed(62, Z)
    assert v60 > 0 and abs(t.speed(40, Z) + v60) < 1e-12
    assert t.speed(50, Z) == 0.0
    assert v60 < t.speed(61, Z) < v62


def test_code_for_minimal_and_deadband():
    t = default_calibration().pan
    assert t.code_for(0.0005, Z) == 50
    assert t.code_for(t.speed(60, Z), Z) == 60
    assert t.code_for(-t.speed(70, Z), Z) == 30
    assert t.code_for(5.0, Z) == 99


def test_code_for_hysteresis():
    t = default_calibration().pan
    want = t.speed(64, Z) * 1.08
    assert t.code_for(want, Z, current=64) == 64
    assert t.code_for(want, Z, current=50) == 65


def test_default_table_constant_deg_s_like_live_he42():
    """Площадка 2026-10-29 (§3б): HE42 держит постоянные °/с на любом зуме («Speed With Zoom Pos.» выключена),
    поворот и наклон одинаково; код 70 — около 13°/с. В долях кадра скорость растёт при приближении как
    hfov(555)/hfov(z). Было: одна точка по зуму — одни и те же fw/s на любом зуме."""
    from ptztracker.control.calibration import hfov_at

    c = default_calibration()
    for z in (0x555, 0x6A9, 0x800, 0x954, 0xAAA, 0xC00, 0xD55, 0xFFF):
        assert abs(c.pan_fw(70, z) * hfov_at(z) / 13.0 - 1) <= 0.1, hex(z)
        grow = c.pan_fw(70, z) / c.pan_fw(70, 0x555)
        assert abs(grow / (hfov_at(0x555) / hfov_at(z)) - 1) <= 0.1, hex(z)
        assert c.pan_fw(70, z, axis="tilt") == c.pan_fw(70, z)
    # прямой замер по #APC на 555 (°/с): таблица мастера калибровки сходится с ним в пределах 10%
    for code, deg in ((55, 0.31), (60, 1.70), (63, 3.65), (65, 6.05), (70, 13.0), (75, 21.5)):
        assert abs(c.pan_fw(code, 0x555) * hfov_at(0x555) / deg - 1) <= 0.1, code


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


def test_position_continuous_after_old_commands_folded():
    # Слежение дольше 10 с: старые команды сворачиваются — положение камеры не должно «прыгать»
    d = CameraDynamics(dead_time=0.08, tau=0.37)
    d.command(0.0, 0.1)
    d.command(5.0, -0.05)
    before = d.position(11.9)
    d.command(12.0, 0.0)  # сворачивает команды старше 10 с
    d.command(16.0, 0.02)
    assert abs(d.position(11.9) - before) < 1e-9
    assert abs(d.displacement(11.0, 11.9) - (d.position(11.9) - d.position(11.0))) < 1e-12


def test_coast_is_stopping_distance():
    """Тормозной путь: команды в пути (мёртвое время) доходят, потом скорость гаснет с τ (путь v·τ)."""
    from ptztracker.control.camera_model import CameraDynamics

    d = CameraDynamics(dead_time=0.08, tau=0.37)
    d.command(0.0, 0.5)
    t = 5.0  # давно едет 0,5: дальше ещё 0,5·(0,08 + 0,37)
    assert abs(d.coast(t) - 0.5 * (0.08 + 0.37)) < 1e-6
    d.command(t, 0.0)  # «стоп» уже отправлен: остаётся тот же путь (стоп ещё в пути)
    assert abs(d.coast(t) - 0.5 * (0.08 + 0.37)) < 1e-6
    far = t + 3.0
    assert abs(d.coast(far)) < 1e-3  # остаток экспоненты
    assert abs(d.position(far) - d.position(t) - 0.5 * (0.08 + 0.37)) < 1e-3


def test_old_calibration_gets_default_latency_for_new_stream_kinds():
    from ptztracker.camera import he42

    d = default_calibration().to_json()
    d["video_latency"] = {"mjpeg": 0.2}  # сохранено до появления MJPEG (2)
    c = Calibration.from_json(d)
    assert c.latency_for("mjpeg") == 0.2
    assert c.latency_for("mjpeg_2") == he42.VIDEO_LATENCY_S["mjpeg_2"]
