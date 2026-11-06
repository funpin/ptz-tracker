"""Окно «Геймпады…»: все виденные геймпады, своя чувствительность у каждого, общий флажок «на крупном плане»."""

from test_gamepad_bridge import PadBackend

from ptztracker.config import ConfigStore
from ptztracker.ui.gamepads_dialog import SHARED_NOTE, has_serial
from ptztracker.ui.main_window import MainWindow

GUID_ID = "030000004c050000e60c000000000000:DualSense Wireless Controller"


class IdPad(PadBackend):
    def info(self):
        return {**super().info(), "id": "A0:B1:C2:D3:E4:F5", "has_serial": True}


def make(qtbot, tmp_path):
    store = ConfigStore(tmp_path / "c.json")
    cfg = store.load()
    cfg.pad_names = {GUID_ID: "DualSense Wireless Controller"}
    cfg.pad_sensitivity = {GUID_ID: 20}
    store.save(cfg)
    b = IdPad()
    w = MainWindow(store, use_sim=True, gamepad_backend=b)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.pad.connected, timeout=2000)
    return w, b


def test_has_serial():
    assert has_serial("A0:B1:C2:D3:E4:F5")
    assert not has_serial(GUID_ID) and not has_serial("?:Pad")


def test_dialog_lists_seen_pads_and_applies_at_once(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    assert w.pads_action.text() == "Геймпады…"
    w.show_gamepads()
    d = w.gamepads_dialog
    assert set(d.sliders) == {GUID_ID, "A0:B1:C2:D3:E4:F5"}
    live = d.status_labels["A0:B1:C2:D3:E4:F5"].text()
    assert "управляет камерой 1" in live and SHARED_NOTE not in live
    old = d.status_labels[GUID_ID].text()
    assert "не подключён" in old and SHARED_NOTE in old
    name = d.name_labels["A0:B1:C2:D3:E4:F5"].text()
    assert "DualSense" in name and "…E4:F5" in name
    assert d.sliders[GUID_ID].value() == 20 and d.sliders["A0:B1:C2:D3:E4:F5"].value() == 50
    d.sliders["A0:B1:C2:D3:E4:F5"].setValue(90)
    assert w.pad.sensitivity == 90  # сразу, без «ОК»
    assert ConfigStore(tmp_path / "c.json").load().pad_sensitivity["A0:B1:C2:D3:E4:F5"] == 90
    d.slow_tele_check.setChecked(False)
    assert not w.cfg.slow_on_tele and not w.controls.slow_tele_check.isChecked()
    w.controls.slow_tele_check.setChecked(True)
    assert d.slow_tele_check.isChecked() and w.cfg.slow_on_tele
    w.close()


def test_dialog_updates_when_pad_unplugged(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w.show_gamepads()
    b.pad = None
    qtbot.waitUntil(lambda: "не подключён" in w.gamepads_dialog.status_labels["A0:B1:C2:D3:E4:F5"].text(),
                    timeout=2000)
    w.close()


def test_empty_dialog(qtbot):
    from ptztracker.ui.gamepads_dialog import GamepadsDialog
    d = GamepadsDialog([], True, lambda *a: None, lambda on: None)
    qtbot.addWidget(d)
    d.show()
    assert d.empty_label.isVisible() and not d.sliders


def test_dialog_l1r1_mode_per_pad(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    pid = "A0:B1:C2:D3:E4:F5"
    w.cfg.pad_gear[pid] = 2
    w._apply_pad_speed(w.pad)
    assert w.pad.speed_limit == 5
    w.show_gamepads()
    d = w.gamepads_dialog
    assert d.mode_combos[pid].currentData() == "limit"  # по умолчанию — предел скорости
    assert "ступень 2 из 8 · до 5 °/с" in d.status_labels[pid].text()
    d.mode_combos[pid].setCurrentIndex(d.mode_combos[pid].findData("sens"))
    assert w.pad.speed_limit == 34  # в режиме чувствительности — ступень по умолчанию
    assert ConfigStore(tmp_path / "c.json").load().pad_l1r1_mode == {pid: "sens"}
    assert "ступень" not in d.status_labels[pid].text()
    d.mode_combos[pid].setCurrentIndex(d.mode_combos[pid].findData("limit"))
    assert w.pad.speed_limit == 5  # ступень запомнена
    w.close()


# --- вид по макету docs/design/mockups/pads-atem.html -------------------------------------------


def _rows():
    from ptztracker.ui.gamepads_dialog import PadRow

    return [PadRow("a0:b1:c2:d3:e4:f5", "DualSense", 40, True, 1, battery=82, battery_time="~5 ч", tally="pgm"),
            PadRow("x1:x2:x3:x4:x5:x6", "Xbox Controller", 50, True, None),
            PadRow(GUID_ID, "DualSense", 70, False)]


def test_card_texts_and_tile(qtbot):
    from ptztracker.ui.gamepads_dialog import GamepadsDialog
    from ptztracker.ui.theme import C

    d = GamepadsDialog(_rows(), True, lambda *a: None, lambda on: None)
    qtbot.addWidget(d)
    live = d.status_labels["a0:b1:c2:d3:e4:f5"].text()
    assert "управляет камерой 1" in live and "заряд средний" in live and "%" not in live and "~5 ч" in live
    assert C.PGM in d.tiles["a0:b1:c2:d3:e4:f5"].styleSheet()
    assert d.tiles["x1:x2:x3:x4:x5:x6"].isHidden()
    assert "выберите клетку" in d.status_labels["x1:x2:x3:x4:x5:x6"].text()
    assert "настройка сохранится" in d.status_labels[GUID_ID].text()
    assert d.done_button.property("variant") == "primary"


def test_curve_preview_follows_slider(qtbot):
    from ptztracker.control.manual_curve import shape
    from ptztracker.ui.gamepads_dialog import GamepadsDialog

    d = GamepadsDialog(_rows(), True, lambda *a: None, lambda on: None)
    qtbot.addWidget(d)
    prev = d.curves["a0:b1:c2:d3:e4:f5"]
    assert prev.sens == 40
    d.sliders["a0:b1:c2:d3:e4:f5"].sliderMoved.emit(90)
    assert prev.sens == 90 and abs(prev.value_at(0.5) - shape(0.5, 90)) < 1e-9


def test_charging_without_level():
    from ptztracker.ui.gamepads_dialog import PadRow, status_text

    t = status_text(PadRow("a0:b1:c2:d3:e4:f5", "DualSense", 50, True, 1, battery=None, charging=True))
    assert "· заряжается" in t and "%" not in t


def test_tele_strength_slider_readout_and_callback(qtbot):
    from ptztracker.ui.gamepads_dialog import GamepadsDialog

    got = []
    d = GamepadsDialog(_rows(), True, lambda *a: None, lambda on: None, tele_strength=50, on_tele_strength=got.append)
    qtbot.addWidget(d)
    assert d.tele_slider.value() == 50 and "39 %" in d.tele_readout.text()
    d.tele_slider.setValue(100)
    assert got == [100] and "15 %" in d.tele_readout.text() and d.tele_graph.strength == 100
    d.slow_tele_check.setChecked(False)
    assert not d.tele_slider.isEnabled()


def test_main_pad_toggle_is_exclusive(qtbot):
    from ptztracker.ui.gamepads_dialog import GamepadsDialog, PadRow

    got = []
    rows = [PadRow("A", "DualSense", 50, True, 1, main=True), PadRow("B", "DualSense", 50, True, 2)]
    d = GamepadsDialog(rows, False, lambda *a: None, lambda *a: None, on_main=got.append)
    qtbot.addWidget(d)
    assert d.main_checks["A"].isChecked() and not d.main_checks["B"].isChecked()
    d.main_checks["B"].click()
    assert got[-1] == "B" and not d.main_checks["A"].isChecked()
    d.main_checks["B"].click()  # сняли — главного нет
    assert got[-1] is None


def test_dialog_tilt_curve_and_speed_per_pad(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    pid = "A0:B1:C2:D3:E4:F5"
    w.show_gamepads()
    d = w.gamepads_dialog
    assert d.tilt_sliders[pid].value() == d.sliders[pid].value()  # не задана — как влево/вправо
    assert d.tilt_speed_sliders[pid].value() == 60
    d.tilt_sliders[pid].setValue(10)
    d.tilt_speed_sliders[pid].setValue(40)
    assert w.pad.tilt_sensitivity == 10 and w.pad.tilt_speed == 40
    assert "40 %" in d.tilt_speed_labels[pid].text()
    cfg = ConfigStore(tmp_path / "c.json").load()
    assert cfg.pad_tilt_sensitivity == {pid: 10} and cfg.pad_tilt_speed == {pid: 40}
    w.close()
