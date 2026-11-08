"""Запись видео в окне: клавиша R, независимость от слежения, флажков и Esc."""

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow


class NoPad:
    def open(self):
        return False


def make(qtbot, tmp_path):
    store = ConfigStore(tmp_path / "c.json")
    w = MainWindow(store, use_sim=True, gamepad_backend=NoPad())
    w.cfg.video_dir = str(tmp_path / "Записи")
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.session.frames.latest() is not None, timeout=5000)
    return w


def press(w, key, text=""):
    for t in (QKeyEvent.KeyPress, QKeyEvent.KeyRelease):
        QApplication.sendEvent(w, QKeyEvent(t, key, Qt.NoModifier, text))


def test_r_toggles_recording_of_selected_camera(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    press(w, Qt.Key_R, "r")
    assert w.session.video_state().active
    other = w.grid.cells[1].session
    assert not other.video_state().active
    press(w, Qt.Key_R, "r")
    assert not w.session.video_state().active
    w.close()


def test_russian_layout_r(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    press(w, ord("К"), "к")
    assert w.session.video_state().active
    w.session.stop_video()
    w.close()


def test_r_keeps_agent_flags_and_tracking(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    cell = w.selected_cell
    cell.gate.set_allowed(True)
    cell.gate.set_off_air(True)
    before = (cell.gate.control_allowed, cell.gate.not_on_air)
    press(w, Qt.Key_R, "r")
    assert (cell.gate.control_allowed, cell.gate.not_on_air) == before
    assert w.session.video_state().active
    w.session.stop_video()
    w.close()


def test_esc_does_not_stop_recording(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    press(w, Qt.Key_R, "r")
    press(w, Qt.Key_Escape)
    time.sleep(0.05)
    assert w.session.video_state().active
    w.session.stop_video()
    w.close()


def test_status_tick_shows_rec_on_preview(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    press(w, Qt.Key_R, "r")
    w._update_status()
    assert w.preview.rec_text().startswith("REC")
    w.session.stop_video()
    w.close()
