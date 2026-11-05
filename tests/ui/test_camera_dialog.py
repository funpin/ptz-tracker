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
    d.ip_edit.setText("10.0.0.9")  # тот же объект камеры по новому адресу: сведения не теряем (сессия уточнит сама)
    assert d.result_config().model == "AW-HE40"


def test_edit_sim_keeps_model_fields(qtbot):
    cfg = CameraConfig(id="s", name="Сим", ip="sim", kind="sim", model="AW-HE42", unsupported=["power"])
    d = CameraDialog(cfg)
    qtbot.addWidget(d)
    r = d.result_config()
    assert (r.model, r.unsupported) == ("AW-HE42", ["power"])
