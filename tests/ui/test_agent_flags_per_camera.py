"""Флажки агента в сетке: ручное действие на камере B снимает их только у B; Esc — у всех.

Условие решения «выбор клетки не снимает флажки» (главная сессия, 2026-11-05)."""

import pytest
from PySide6.QtCore import Qt

from ptztracker.config import ConfigStore
from ptztracker.gamepad.intents import Preset
from ptztracker.ui.main_window import MainWindow


class NoPad:
    def open(self):
        return False


@pytest.fixture
def w(qtbot, tmp_path):
    win = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True, gamepad_backend=NoPad())
    qtbot.addWidget(win)
    win.show()
    yield win
    win.close()


def allow_both(w):
    for c in w.grid.cells[:2]:
        c.gate.set_allowed(True)
        c.gate.set_off_air(True)


def flags(w, slot):
    g = w.grid.cells[slot].gate
    return g.control_allowed, g.not_on_air


def test_keyboard_on_b_revokes_only_b(qtbot, w):
    allow_both(w)
    w.select_slot(1)
    assert flags(w, 0) == (True, True)  # выбор клетки флажков не трогает
    qtbot.keyPress(w, Qt.Key_Right)
    qtbot.keyRelease(w, Qt.Key_Right)
    assert flags(w, 1) == (False, False)
    assert flags(w, 0) == (True, True)


def test_mouse_joystick_on_b_revokes_only_b(w):
    allow_both(w)
    w.select_slot(1)
    w.controls._on_joystick(0.5, 0.0)
    w.controls.release_all()
    assert flags(w, 1) == (False, False)
    assert flags(w, 0) == (True, True)


def test_panel_preset_on_b_revokes_only_b(w):
    allow_both(w)
    w.select_slot(1)
    w.controls._preset_clicked(1)
    assert flags(w, 1) == (False, False)
    assert flags(w, 0) == (True, True)


def test_pad_stick_on_b_revokes_only_b(w):
    allow_both(w)
    assert w.pad_connected(bridge=True) == 1
    w.pad_create(1)  # геймпад — на камеру клетки 2; выбранной остаётся клетка 1
    assert w.pad_router.slot_of(1) == 1 and w.grid.selected == 0
    w.pad_velocity(1, 0.5, 0.0, 0.0)
    w.pad_velocity(1, 0.0, 0.0, 0.0)
    assert flags(w, 1) == (False, False)
    assert flags(w, 0) == (True, True)


def test_pad_preset_on_b_revokes_only_b(w):
    allow_both(w)
    w.pad_connected(bridge=True)
    w.pad_create(1)
    w._on_pad_intents([Preset(0, False)])
    assert flags(w, 1) == (False, False)
    assert flags(w, 0) == (True, True)


def test_escape_revokes_all(qtbot, w):
    allow_both(w)
    qtbot.keyPress(w, Qt.Key_Escape)
    assert flags(w, 0) == (False, False)
    assert flags(w, 1) == (False, False)
