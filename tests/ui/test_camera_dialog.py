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
