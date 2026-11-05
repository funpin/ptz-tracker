import math

from ptztracker.camera import he42
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
    for z in (0x555, 0x6A9, 0x800, 0x954, 0xAAA, 0xC00, 0xD55):  # выше 0xD54 — замедление на теле, тесты ниже
        assert abs(c.pan_fw(70, z) * hfov_at(z) / 13.0 - 1) <= 0.1, hex(z)
        grow = c.pan_fw(70, z) / c.pan_fw(70, 0x555)
        assert abs(grow / (hfov_at(0x555) / hfov_at(z)) - 1) <= 0.1, hex(z)
        assert c.pan_fw(70, z, axis="tilt") == c.pan_fw(70, z)
    # прямой замер по #APC на 555 (°/с): таблица мастера калибровки сходится с ним в пределах 10%
    for code, deg in ((55, 0.31), (60, 1.70), (63, 3.65), (65, 6.05), (70, 13.0), (75, 21.5)):
        assert abs(c.pan_fw(code, 0x555) * hfov_at(0x555) / deg - 1) <= 0.1, code


def test_default_table_follows_tele_slowdown_above_d54():
    """AW-HE40 (клуб 2026-10-30): выше 0xD54 поворот медленнее (he42.TELE_PAN_DEG_S). Код 70 на FFF — 2,5 °/с,
    не 13,6; до 0xD54 таблица прежняя."""
    from ptztracker.control.calibration import hfov_at

    c = default_calibration()
    assert abs(c.pan_fw(70, 0xFFF) * hfov_at(0xFFF) / 2.47 - 1) <= 0.02
    assert abs(c.pan_fw(63, 0xFFF) * hfov_at(0xFFF) / 1.20 - 1) <= 0.02
    assert abs(c.pan_fw(75, 0xEA9) * hfov_at(0xEA9) / 9.92 - 1) <= 0.03
    assert abs(c.pan_fw(70, 0xD54) * hfov_at(0xD54) / 13.62 - 1) <= 0.02  # без изменений
    # на 0xF00 между EA9 и FFF — между их °/с
    deg = c.pan_fw(75, 0xF00) * hfov_at(0xF00)
    assert 5.10 < deg < 9.92


def test_saved_calibration_without_tele_points_still_slows_above_d54():
    """Калибровка, снятая на низких зумах (до 0xD54), не знает про тело: выше 0xD54 к ней применяется то же
    замедление; внутри измеренного диапазона и выше своей верхней точки с замером на теле — ничего лишнего."""
    t = SpeedTable({0x555: [(63, 0.06), (70, 0.2)], 0x9AA: [(63, 0.15), (70, 0.5)]})
    assert t.speed(70, 0x9AA) == 0.5  # внутри измеренного — как есть
    assert t.speed(70, 0xD54) == 0.5  # до 0xD54 — прежнее поведение (крайняя таблица)
    assert abs(t.speed(70, 0xFFF) / (0.5 * he42.tele_pan_ratio(70, 0xFFF)) - 1) < 1e-9
    assert abs(t.speed(-70 + 100, 0xFFF) + t.speed(70, 0xFFF)) < 1e-12  # зеркально
    # есть точка на теле (замер уже включает замедление): над ней дополнительно не замедляем
    t2 = SpeedTable({0x555: [(70, 0.2)], 0xFFF: [(70, 0.7)]})
    assert t2.speed(70, 0xFFF) == 0.7
    t3 = SpeedTable({0x555: [(70, 0.2)]})  # единственная точка, зум выше неё и выше 0xD54
    assert abs(t3.speed(70, 0xFFF) / (0.2 * he42.tele_pan_ratio(70, 0xFFF)) - 1) < 1e-9
    assert t3.speed(70, 0xA00) == 0.2


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


def test_measured_tilt_replaces_pan_table_for_tilt_axis():
    """Наклон, измеренный мастером (доля от поворота по коду), заменяет допущение «наклон = поворот»: скорость,
    код и потолок оси tilt — по нему; поворот не меняется. Между кодами — линейно, за крайними — крайний."""
    c = default_calibration()
    pan70, pan_max = c.pan_fw(70, Z), c.pan_max_fw(Z)
    c.tilt = [(63, 0.8), (75, 0.6)]
    assert c.pan_fw(70, Z) == pan70  # поворот прежний
    r70 = 0.8 + (0.6 - 0.8) * (70 - 63) / (75 - 63)
    assert abs(c.pan_fw(70, Z, axis="tilt") / (r70 * pan70) - 1) < 1e-9
    assert abs(c.pan_fw(30, Z, axis="tilt") + c.pan_fw(70, Z, axis="tilt")) < 1e-12  # зеркально
    assert abs(c.pan_fw(55, Z, axis="tilt") / (0.8 * c.pan_fw(55, Z)) - 1) < 1e-9  # ниже 63 — доля 63
    assert abs(c.pan_max_fw(Z, axis="tilt") / (0.6 * pan_max) - 1) < 1e-9  # выше 75 — доля 75
    for code in (60, 63, 67, 70, 75, 80):
        v = c.pan_fw(code, Z, axis="tilt")
        assert c.pan_code(v, Z, axis="tilt") == code, code
        assert c.pan_code(-v, Z, axis="tilt") == 100 - code, code
    c.tilt_scale = 1.5  # поправка слежения — поверх замера
    assert abs(c.pan_fw(70, Z, axis="tilt") / (1.5 * r70 * pan70) - 1) < 1e-9


def test_calibration_metadata_roundtrip_and_old_json_loads():
    """Модель, прошивка, поток, дата, зум-точки и наклон сохраняются; калибровка, сохранённая до них, читается
    как прежде (наклон — как поворот)."""
    c = default_calibration()
    c.model, c.firmware, c.stream_kind, c.created = "AW-HE42", None, "mjpeg", "2026-11-05T10:00:00"
    c.zoom_points = [0x555, 0x800, 0xD55, 0xFFF]
    c.tilt = [(63, 0.9), (75, 0.95)]
    c2 = Calibration.from_json(c.to_json())
    assert (c2.model, c2.firmware, c2.stream_kind, c2.created) == ("AW-HE42", None, "mjpeg", "2026-11-05T10:00:00")
    assert c2.zoom_points == [0x555, 0x800, 0xD55, 0xFFF]
    assert c2.tilt == [(63, 0.9), (75, 0.95)]
    assert c2.pan_fw(70, Z, axis="tilt") == c.pan_fw(70, Z, axis="tilt")
    old = {k: v for k, v in c.to_json().items()
           if k not in ("model", "firmware", "stream_kind", "zoom_points", "tilt")}
    c3 = Calibration.from_json(old)
    assert c3.model is None and c3.firmware is None and c3.stream_kind is None and c3.tilt is None
    assert c3.zoom_points == sorted(c3.pan.points)  # у старой — по таблице
    assert c3.pan_fw(70, Z, axis="tilt") == c3.pan_fw(70, Z)
    assert default_calibration().tilt is None
