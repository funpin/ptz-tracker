"""Окно «Горячие клавиши» (F1) и чип геймпада в верхней панели."""

from PySide6.QtCore import Qt
from test_gamepad_bridge import PadBackend, make

from ptztracker.config import CameraConfig
from ptztracker.ui.hotkeys_dialog import GAMEPAD_HELP, HotkeysDialog, fmt_remaining
from ptztracker.ui.main_window import _HOTKEYS


def rows(table):
    return [(table.item(r, 0).text(), table.item(r, 1).text()) for r in range(table.rowCount())]


def test_dialog_tables(qtbot):
    d = HotkeysDialog()
    qtbot.addWidget(d)
    assert rows(d.keys_table) == [(k, v) for k, v in _HOTKEYS] + [("F1", "эта справка")]
    pad = rows(d.pad_table)
    assert pad == GAMEPAD_HELP
    text = {k.lower(): v.lower() for k, v in pad}
    assert any("тачпад" in k and "стоп камеры" in v for k, v in text.items())
    assert any("r3" in k and "слежение" in v for k, v in text.items())
    assert any("create" in k and "следующая камера" in v for k, v in text.items())
    assert any("create" in k and "момент" in v for k, v in text.items())
    assert any("options" in k and "меню" in v for k, v in text.items())


def test_f1_opens_hotkeys_with_gamepad_table(qtbot, tmp_path):
    w, _b = make(qtbot, tmp_path)
    qtbot.keyClick(w, Qt.Key_F1)
    d = w.hotkeys_dialog
    assert d is not None and d.isVisible()
    assert any("тачпад" in k.lower() for k, _ in rows(d.pad_table))
    d.close()
    w.close()


def test_fmt_remaining():
    assert fmt_remaining(3 * 3600 + 10 * 60) == "≈3 ч 10 мин"
    assert fmt_remaining(45 * 60) == "≈45 мин"
    assert fmt_remaining(10) == "≈1 мин"


def test_gamepad_chip_hidden_without_device(qtbot, tmp_path):
    w, _b = make(qtbot, tmp_path, backend=PadBackend(fail=True))
    assert w.pad_chip.isHidden()
    w.close()


def test_gamepad_chip_shows_number_and_battery(qtbot, tmp_path):
    cams = [CameraConfig(id="a", name="KZ_PTZ_2", ip="10.0.0.2", pad_number=None)]
    w, b = make(qtbot, tmp_path, cameras=cams)
    assert not w.pad_chip.isHidden()
    assert w.pad_chip.full_text == "1"
    w.pad._on_batt(60, False)
    assert w.pad_chip.full_text == "1 · 60% · считаю…"
    w.pad._battery.remaining_s = lambda: 3 * 3600 + 600
    w.pad._on_batt(60, False)
    assert w.pad_chip.full_text == "1 · 60% · ≈3 ч 10 мин"
    w.pad._on_batt(60, True)
    assert w.pad_chip.full_text == "1 · 60% · заряжается"
    w.pad._on_batt(None, True)  # SDL при зарядке уровня не даёт
    assert w.pad_chip.full_text == "1 · заряжается"
    w.pad._on_batt(None, None)
    assert w.pad_chip.full_text == "1"
    w.close()


def test_gamepad_chip_hides_on_disconnect(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w.pad._on_conn(None)
    assert w.pad_chip.isHidden()
    w.close()
