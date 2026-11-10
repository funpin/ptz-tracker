from ptztracker.config import CameraConfig
from ptztracker.ui.videohub_dialog import VideohubDialog
from ptztracker.videohub.client import HubSnapshot
from ptztracker.videohub.protocol import HubState

LIVE = HubSnapshot(True, HubState(present=True, model="Smart Videohub 20 x 20", inputs=20, outputs=20,
                                  input_labels={3: "PTZ 1", 4: "PTZ 2"}, output_labels={5: "Монитор"},
                                  routing={5: 3}, locks={5: "U"}))


def cams():
    return [(1, CameraConfig(id="a", name="Сцена", ip="10.0.0.12", videohub_input=3)),
            (2, CameraConfig(id="b", name="Зал", ip="10.0.0.13"))]


def make(qtbot, snap=LIVE, ip="10.10.10.50", output=5, follow=True, cameras=None):
    d = VideohubDialog(ip, output, follow, cameras if cameras is not None else cams(), snapshot=lambda: snap)
    qtbot.addWidget(d)
    return d


def test_live_lists_have_hub_labels_and_current(qtbot):
    d = make(qtbot)
    assert d.output_combo.currentText() == "5 · Монитор"
    assert d.output_combo.count() == 21  # «не задан» + 20
    assert d.input_combos[0].currentText() == "3 · PTZ 1"
    assert d.input_combos[1].currentText() == "не переключать"
    assert d.status_label.text() == "На связи · Smart Videohub 20 x 20"
    assert d.now_label.text() == "Сейчас на выходе 5: Сцена"


def test_offline_numbers_only_and_keeps_big_current(qtbot):
    d = make(qtbot, snap=HubSnapshot(), output=60)
    assert d.output_combo.currentText() == "60"
    assert d.output_combo.count() == 61
    assert d.status_label.text().startswith("Нет связи")
    assert d.now_label.text() == "Сейчас на выходе 60: неизвестно"


def test_values_and_camera_inputs(qtbot):
    d = make(qtbot)
    d.output_combo.setCurrentIndex(d.output_combo.findData(7))
    d.input_combos[1].setCurrentIndex(d.input_combos[1].findData(4))
    d.input_combos[0].setCurrentIndex(0)
    d.follow_check.setChecked(False)
    d.accept()
    assert d.values() == ("10.10.10.50", 7, False)
    assert [(c.id, i) for c, i in d.camera_inputs()] == [("a", 0), ("b", 4)]


def test_selection_survives_hub_coming_online(qtbot):
    state = {"snap": HubSnapshot()}
    d = VideohubDialog("10.10.10.50", 5, True, cams(), snapshot=lambda: state["snap"])
    qtbot.addWidget(d)
    d.input_combos[1].setCurrentIndex(d.input_combos[1].findData(4))
    state["snap"] = LIVE
    d._refresh()
    assert d.input_combos[1].currentText() == "4 · PTZ 2"
    assert d.output_combo.currentText() == "5 · Монитор"


def test_turn_off_and_bad_address(qtbot):
    d = make(qtbot)
    d.ip_edit.setText("10.10.10")
    d.accept()
    assert d.result() == 0 and d.error_label.isVisibleTo(d)
    d.off_button.click()
    assert d.values()[0] == "" and d.result() == 1


def test_changed_address_does_not_show_old_hub(qtbot):
    d = make(qtbot)
    d.ip_edit.setText("10.10.10.51")
    assert "Адрес изменён" in d.status_label.text()
    assert d.now_label.text() == "Сейчас на выходе 5: неизвестно"


def test_locked_output_explained(qtbot):
    snap = HubSnapshot(True, HubState(present=True, inputs=20, outputs=20, locks={5: "L"}))
    d = make(qtbot, snap=snap)
    assert "заблокирован" in d.now_label.text()


def test_no_cameras_hides_panel(qtbot):
    d = make(qtbot, cameras=[])
    assert not d.cams_panel.isVisibleTo(d)
