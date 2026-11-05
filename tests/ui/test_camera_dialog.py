import dataclasses

from ptztracker.config import CameraConfig
from ptztracker.ui.camera_dialog import CameraDialog


def test_dialog_rejects_bad_ip(qtbot):
    d = CameraDialog()
    qtbot.addWidget(d)
    d.name_edit.setText("Сцена-3")
    d.ip_edit.setText("10.0.0")
    assert not d.ok_button.isEnabled()
    d.ip_edit.setText("10.0.0.14")
    assert d.ok_button.isEnabled()


def test_dialog_requires_name(qtbot):
    d = CameraDialog()
    qtbot.addWidget(d)
    d.ip_edit.setText("10.0.0.14")
    d.name_edit.setText("  ")
    assert not d.ok_button.isEnabled()


def test_dialog_result_keeps_id_and_fields(qtbot):
    d = CameraDialog(CameraConfig(id="cam-7", name="А", ip="10.0.0.7"))
    qtbot.addWidget(d)
    d.user_edit.setText("admin")
    d.pass_edit.setText("secret")
    d.rtsp_edit.setText("")
    d.interval_spin.setValue(150)
    c = d.result_config()
    assert (c.id, c.ip, c.username, c.rtsp_url, c.min_interval) == ("cam-7", "10.0.0.7", "admin", None, 0.150)
    assert d.password() == "secret"


def test_new_camera_gets_unique_id(qtbot):
    d1, d2 = CameraDialog(), CameraDialog()
    qtbot.addWidget(d1)
    qtbot.addWidget(d2)
    for d in (d1, d2):
        d.name_edit.setText("Н")
        d.ip_edit.setText("10.0.0.20")
    assert d1.result_config().id != d2.result_config().id


def test_dialog_pad_number(qtbot):
    d = CameraDialog(CameraConfig(id="c", name="А", ip="10.0.0.7"))
    qtbot.addWidget(d)
    assert d.result_config().pad_number is None  # пусто — автоматически
    d.pad_spin.setValue(3)
    assert d.result_config().pad_number == 3
    d2 = CameraDialog(CameraConfig(id="c", name="А", ip="10.0.0.7", pad_number=4))
    qtbot.addWidget(d2)
    assert d2.pad_spin.value() == 4
    d2.pad_spin.setValue(d2.pad_spin.minimum())
    assert d2.result_config().pad_number is None


def test_edit_preserves_other_fields(qtbot):
    cfg = CameraConfig(id="c", name="А", ip="10.0.0.7", calibration={"k": 1}, speed_scale={"pan": 1.2},
                       composition="full", effort=0.9, stream="mjpeg_2")
    d = CameraDialog(cfg)
    qtbot.addWidget(d)
    d.name_edit.setText("Б")
    r = d.result_config()
    assert r.name == "Б" and r.id == "c"
    assert (r.calibration, r.speed_scale, r.composition, r.effort, r.stream) == (
        {"k": 1}, {"pan": 1.2}, "full", 0.9, "mjpeg_2")


def _model_text(d):
    return d.model_label.text() if not d.model_label.isHidden() else None


def test_dialog_shows_model_and_firmware_read_only(qtbot):
    d = CameraDialog(CameraConfig(id="c", name="А", ip="10.0.0.7", model="AW-HE40", firmware="1.54"))
    qtbot.addWidget(d)
    assert _model_text(d) == "AW-HE40 · прошивка 1.54"


def test_dialog_model_without_firmware_and_unknown(qtbot):
    d = CameraDialog(CameraConfig(id="c", name="А", ip="10.0.0.7", model="AW-HE42"))
    qtbot.addWidget(d)
    assert _model_text(d) == "AW-HE42"
    d2 = CameraDialog(CameraConfig(id="c", name="А", ip="10.0.0.7"))
    qtbot.addWidget(d2)
    assert "не определена" in _model_text(d2)


def test_dialog_new_camera_and_sim_have_no_model_row(qtbot):
    d = CameraDialog()
    qtbot.addWidget(d)
    assert _model_text(d) is None
    d.kind_combo.setCurrentIndex(d.kind_combo.findData("sim"))
    assert _model_text(d) is None
    s = CameraDialog(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"))
    qtbot.addWidget(s)
    assert _model_text(s) is None


def test_edit_keeps_model_firmware_and_unsupported(qtbot):
    cfg = CameraConfig(id="c", name="А", ip="10.0.0.7", model="AW-HE40", firmware="1.54",
                       unsupported=["gain", "wb"])
    d = CameraDialog(cfg)
    qtbot.addWidget(d)
    d.name_edit.setText("Б")
    d.interval_spin.setValue(200)
    r = d.result_config()
    assert (r.model, r.firmware, r.unsupported) == ("AW-HE40", "1.54", ["gain", "wb"])
    assert r.unsupported is not cfg.unsupported  # копия, не общий список
    r.unsupported.append("power")
    assert cfg.unsupported == ["gain", "wb"]


def test_edit_ip_forgets_model_firmware_and_unsupported(qtbot):
    """По новому адресу может ответить другая камера: сведения о прежней не годятся (сессия определит заново)."""
    cfg = CameraConfig(id="c", name="А", ip="10.0.0.7", model="AW-HE40", firmware="1.54", unsupported=["gain"],
                       calibration={"k": 1}, speed_scale={"pan": 1.2})
    d = CameraDialog(cfg)
    qtbot.addWidget(d)
    d.ip_edit.setText("10.0.0.9")
    r = d.result_config()
    assert (r.model, r.firmware, r.unsupported) == (None, None, [])
    assert (r.calibration, r.speed_scale) == ({"k": 1}, {"pan": 1.2})  # калибровку сверит подсказка «откалибруйте»
    assert cfg.model == "AW-HE40" and cfg.unsupported == ["gain"]


def test_edit_sim_keeps_model_fields(qtbot):
    cfg = CameraConfig(id="s", name="Сим", ip="sim", kind="sim", model="AW-HE42", unsupported=["power"])
    d = CameraDialog(cfg)
    qtbot.addWidget(d)
    r = d.result_config()
    assert (r.model, r.unsupported) == ("AW-HE42", ["power"])


def _full_cfg(**kw):
    from ptztracker.control.camera_model import default_calibration

    c = default_calibration()
    c.model, c.firmware, c.stream_kind, c.created = "AW-HE40", "1.54", "mjpeg_2", "2026-11-05T14:30:00"
    c.zoom_points = [0x555, 0x800, 0xD54]
    c.tilt = [(60, 0.9), (99, 0.85)]
    base = dict(id="c", name="А", ip="10.0.0.7", model="AW-HE40", firmware="1.54", unsupported=["gain", "color_temp"],
                calibration=c.to_json(), speed_scale={"pan": 1.4, "tilt": 1.0, "zoom": 0.9},
                composition="full", effort=0.9, stream="mjpeg_2", atem_input=3, pad_number=2)
    base.update(kw)
    return CameraConfig(**base)


def _info(d):
    return {k: v.text() for k, v in d.info_values.items()}


def test_info_tab_shows_everything_read_only(qtbot):
    d = CameraDialog(_full_cfg())
    qtbot.addWidget(d)
    d.show()
    assert d.tabs.isTabVisible(1)
    t = _info(d)
    assert t["model"] == "AW-HE40 · прошивка 1.54"
    assert "2026" in t["calib_date"] and "05.11.2026" in t["calib_date"]
    assert "1280×720" in t["calib_stream"]
    assert t["calib_model"] == "AW-HE40 · прошивка 1.54"
    assert t["calib_zoom"].startswith("3 точки (от широкого до ×") and "0x" not in t["calib_zoom"]
    assert t["calib_tilt"] == "измерен"
    assert "1,40" in t["speed_scale"] and "0,90" in t["speed_scale"]
    assert "Gain" in t["unsupported"] and "температура" in t["unsupported"]
    assert d.reset_button.isEnabled()


def test_info_tab_without_calibration(qtbot):
    d = CameraDialog(CameraConfig(id="c", name="А", ip="10.0.0.7"))
    qtbot.addWidget(d)
    t = _info(d)
    assert "не делалась" in t["calib_date"]
    assert t["calib_tilt"] == "—"
    assert "не подстраивалась" in t["speed_scale"]
    assert t["unsupported"] == "нет"
    assert not d.reset_button.isEnabled()


def test_info_tab_old_calibration_has_no_model(qtbot):
    d = CameraDialog(_full_cfg(calibration={"pan": {}, "zoom": [], "created": "2026-10-01T10:00:00"}))
    qtbot.addWidget(d)
    t = _info(d)
    assert "прежней версии" in t["calib_model"]
    assert t["calib_tilt"] == "не измерен"


def test_info_tab_survives_broken_calibration(qtbot):
    d = CameraDialog(_full_cfg(calibration={"zoom_points": "x", "tilt": 5}, speed_scale={"pan": "много"}))
    qtbot.addWidget(d)
    assert _info(d)["calib_date"]  # не падает


def test_info_tab_hidden_for_new_and_sim(qtbot):
    d = CameraDialog()
    qtbot.addWidget(d)
    assert d.tabs.count() == 1 or not d.tabs.isTabVisible(1)
    s = CameraDialog(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"))
    qtbot.addWidget(s)
    assert s.tabs.count() == 1 or not s.tabs.isTabVisible(1)


def test_reset_calibration_asks_and_clears_only_calibration_and_speed(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    d = CameraDialog(_full_cfg())
    qtbot.addWidget(d)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    d.reset_button.click()
    r = d.result_config()
    assert r.calibration is not None and r.speed_scale is not None  # «Нет» ничего не сбросило
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    d.reset_button.click()
    assert not d.reset_button.isEnabled() and "сброшена" in _info(d)["calib_date"]
    r = d.result_config()
    assert (r.calibration, r.speed_scale) == (None, None)
    assert (r.model, r.firmware, r.unsupported, r.composition, r.effort, r.stream, r.atem_input, r.pad_number) == (
        "AW-HE40", "1.54", ["gain", "color_temp"], "full", 0.9, "mjpeg_2", 3, 2)


def test_reset_is_not_applied_on_cancel(qtbot, monkeypatch):
    """Диалог ничего не пишет сам: без «ОК» исходная камера не тронута."""
    from PySide6.QtWidgets import QMessageBox

    cfg = _full_cfg()
    d = CameraDialog(cfg)
    qtbot.addWidget(d)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    d.reset_button.click()
    d.reject()
    assert cfg.calibration is not None and cfg.speed_scale is not None


def test_edit_keeps_everything(qtbot):
    cfg = _full_cfg()
    d = CameraDialog(cfg)
    qtbot.addWidget(d)
    d.name_edit.setText("Б")
    d.ip_edit.setText("10.0.0.7")
    d.interval_spin.setValue(200)
    r = d.result_config()
    keep = ("calibration", "speed_scale", "composition", "effort", "stream", "model", "firmware", "unsupported",
            "atem_input", "pad_number")
    assert {k: getattr(r, k) for k in keep} == {k: getattr(cfg, k) for k in keep}
    assert (r.name, r.ip) == ("Б", "10.0.0.7")


def test_merge_edit_applies_only_dialog_changes_over_newer_state(qtbot):
    """Пока диалог был открыт, окно узнало новую модель и сохранило поправку: итог диалога их не затирает."""
    from ptztracker.ui.camera_dialog import merge_edit

    cfg = _full_cfg(model=None, firmware=None, unsupported=[], speed_scale=None)
    d = CameraDialog(cfg)
    qtbot.addWidget(d)
    d.name_edit.setText("Б")
    latest = _full_cfg(model="AW-HE42", firmware=None, unsupported=["power"], speed_scale={"pan": 1.1})
    m = merge_edit(latest, d.snapshot, d.result_config())
    assert m.name == "Б"
    assert (m.model, m.unsupported, m.speed_scale) == ("AW-HE42", ["power"], {"pan": 1.1})
    d.ip_edit.setText("10.0.0.9")  # другой адрес: сведения о прежней камере (в том числе новые) забываются
    m = merge_edit(latest, d.snapshot, d.result_config())
    assert (m.ip, m.model, m.unsupported) == ("10.0.0.9", None, [])
    d.ip_edit.setText("10.0.0.7")
    m = merge_edit(latest, d.snapshot, d.result_config())
    m.unsupported.append("x")
    assert latest.unsupported == ["power"]


def test_merge_edit_reset_forces_none_even_if_snapshot_had_no_speed_scale(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ptztracker.ui.camera_dialog import merge_edit

    cfg = _full_cfg(speed_scale=None)
    d = CameraDialog(cfg)
    qtbot.addWidget(d)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    d.reset_button.click()
    latest = dataclasses.replace(cfg, speed_scale={"pan": 1.3})  # слежение успело записать
    m = merge_edit(latest, d.snapshot, d.result_config(), d.reset_calibration)
    assert (m.calibration, m.speed_scale) == (None, None)
