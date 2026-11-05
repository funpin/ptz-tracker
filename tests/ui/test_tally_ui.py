import dataclasses
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tally"))
from fake_atem import FakeAtem  # noqa: E402

from ptztracker.config import CameraConfig, ConfigStore  # noqa: E402
from ptztracker.ui import main_window as mw  # noqa: E402
from ptztracker.ui.atem_dialog import AtemDialog  # noqa: E402
from ptztracker.ui.camera_dialog import CameraDialog  # noqa: E402
from ptztracker.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def atem():
    fake = FakeAtem()
    yield fake
    fake.close()


def test_tally_frame_red_when_camera_in_pgm(qtbot, tmp_path, atem, monkeypatch):
    monkeypatch.setattr(mw, "SIM_CAMERA", dataclasses.replace(mw.SIM_CAMERA, atem_input=2))
    store = ConfigStore(tmp_path / "c.json")
    cfg = store.load()
    cfg.atem_ip = "127.0.0.1"
    store.save(cfg)
    w = MainWindow(store, use_sim=True, atem_port=atem.port)
    qtbot.addWidget(w)
    w.show()
    try:
        qtbot.waitUntil(lambda: not w.atem_pill.isHidden(), timeout=2000)
        atem.set_tally((0, 1))
        qtbot.waitUntil(lambda: w.video_frame.tally() == "pgm", timeout=2000)
        assert "PGM" in w.atem_pill.text() and "ЭФИР" in w.atem_pill.toolTip()
        atem.set_tally((0, 0))
        qtbot.waitUntil(lambda: w.video_frame.tally() is None, timeout=2000)
        assert "PGM" not in w.atem_pill.text()
    finally:
        w.close()
    assert w.atem is None


def test_tally_chip_warns_when_input_out_of_range(qtbot, tmp_path, atem, monkeypatch):
    monkeypatch.setattr(mw, "SIM_CAMERA", dataclasses.replace(mw.SIM_CAMERA, atem_input=5))
    store = ConfigStore(tmp_path / "c.json")
    cfg = store.load()
    cfg.atem_ip = "127.0.0.1"
    store.save(cfg)
    atem.set_tally((0, 1))  # микшер знает только 2 входа
    w = MainWindow(store, use_sim=True, atem_port=atem.port)
    qtbot.addWidget(w)
    w.show()
    try:
        qtbot.waitUntil(lambda: "вне диапазона" in w.atem_pill.toolTip(), timeout=3000)
        assert w.atem_pill.toolTip() == "ATEM · вход 5 вне диапазона"
        assert w.atem_pill.kind() == "warn"
        assert w.video_frame.tally() is None
    finally:
        w.close()


def test_no_atem_no_chip(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    qtbot.wait(250)
    assert w.atem is None
    assert w.atem_pill.isHidden()
    assert w.video_frame.tally() is None
    w.close()


def test_camera_dialog_keeps_atem_input(qtbot):
    cfg = CameraConfig(id="a", name="Сцена", ip="10.0.0.5", atem_input=5)
    d = CameraDialog(cfg)
    qtbot.addWidget(d)
    assert d.atem_spin.value() == 5
    d.atem_spin.setValue(3)
    assert d.result_config().atem_input == 3
    d.atem_spin.setValue(0)
    assert d.atem_spin.text() == "нет"
    assert d.result_config().atem_input == 0


def test_atem_dialog_rejects_bad_ip(qtbot):
    d = AtemDialog("")
    qtbot.addWidget(d)
    d.ip_edit.setText(" 10.0.0 ")
    d.accept()
    assert d.result() != QDialog.Accepted
    assert not d.error_label.isHidden() or d.error_label.text()
    d.ip_edit.setText(" 10.0.0.9 ")
    d.accept()
    assert d.result() == QDialog.Accepted
    assert d.ip() == "10.0.0.9"


def test_atem_dialog_empty_is_allowed(qtbot):
    d = AtemDialog("10.0.0.9")
    qtbot.addWidget(d)
    d.ip_edit.setText("")
    d.accept()
    assert d.result() == QDialog.Accepted and d.ip() == ""


# --- вид по макету docs/design/mockups/pads-atem.html -------------------------------------------


def test_atem_dialog_status_and_cameras(qtbot):
    from ptztracker.ui.theme import C

    d = AtemDialog("10.0.0.9", status=lambda: ("на связи", "ok"),
                   cameras=lambda: [(1, "Сцена", 3, "pgm"), (2, "Зал", 1, "pvw"), (3, "Кафедра", 0, None)])
    qtbot.addWidget(d)
    d.show()
    assert "На связи" in d.status_label.text() and C.LIME in d.status_dot.styleSheet()
    texts = [r.text() for r in d.camera_labels]
    assert any("вход 3" in t for t in texts) and any("вход не задан" in t for t in texts)
    d.ip_edit.setText("10.0.0.10")
    assert "после «Сохранить»" in d.status_label.text()
    assert d.ok_button.text() == "Сохранить" and d.ok_button.property("variant") == "primary"


def test_atem_off_button_clears_and_saves(qtbot):
    d = AtemDialog("10.0.0.9")
    qtbot.addWidget(d)
    d.off_button.click()
    assert d.result() == QDialog.Accepted and d.ip() == ""
