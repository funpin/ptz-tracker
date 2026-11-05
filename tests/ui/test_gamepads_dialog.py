"""Окно «Геймпады…»: все виденные геймпады, своя чувствительность у каждого, общий флажок «на крупном плане»."""

from test_gamepad_bridge import PadBackend

from ptztracker.config import ConfigStore
from ptztracker.ui.gamepads_dialog import SHARED_NOTE, has_serial
from ptztracker.ui.main_window import MainWindow

GUID_ID = "030000004c050000e60c000000000000:DualSense Wireless Controller"


class IdPad(PadBackend):
    def info(self):
        return {**super().info(), "id": "a0:b1:c2:d3:e4:f5", "has_serial": True}


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
    assert has_serial("a0:b1:c2:d3:e4:f5")
    assert not has_serial(GUID_ID) and not has_serial("?:Pad")


def test_dialog_lists_seen_pads_and_applies_at_once(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    assert w.pads_action.text() == "Геймпады…"
    w.show_gamepads()
    d = w.gamepads_dialog
    assert set(d.sliders) == {GUID_ID, "a0:b1:c2:d3:e4:f5"}
    live = d.status_labels["a0:b1:c2:d3:e4:f5"].text()
    assert "подключён · камера 1" in live and SHARED_NOTE not in live
    old = d.status_labels[GUID_ID].text()
    assert "не подключён" in old and SHARED_NOTE in old
    names = [w_.text() for i in range(d._grid.count()) if hasattr(w_ := d._grid.itemAt(i).widget(), "text")]
    assert "DualSense · …e4:f5" in names
    assert d.sliders[GUID_ID].value() == 20 and d.sliders["a0:b1:c2:d3:e4:f5"].value() == 50
    d.sliders["a0:b1:c2:d3:e4:f5"].setValue(90)
    assert w.pad.sensitivity == 90  # сразу, без «ОК»
    assert ConfigStore(tmp_path / "c.json").load().pad_sensitivity["a0:b1:c2:d3:e4:f5"] == 90
    d.slow_tele_check.setChecked(False)
    assert not w.cfg.slow_on_tele and not w.controls.slow_tele_check.isChecked()
    w.controls.slow_tele_check.setChecked(True)
    assert d.slow_tele_check.isChecked() and w.cfg.slow_on_tele
    w.close()


def test_dialog_updates_when_pad_unplugged(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w.show_gamepads()
    b.pad = None
    qtbot.waitUntil(lambda: "не подключён" in w.gamepads_dialog.status_labels["a0:b1:c2:d3:e4:f5"].text(),
                    timeout=2000)
    w.close()


def test_empty_dialog(qtbot):
    from ptztracker.ui.gamepads_dialog import GamepadsDialog
    d = GamepadsDialog([], True, lambda *a: None, lambda on: None)
    qtbot.addWidget(d)
    d.show()
    assert d.empty_label.isVisible() and not d.sliders
