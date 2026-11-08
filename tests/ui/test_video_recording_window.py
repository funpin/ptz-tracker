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


def test_bottom_pill_follows_recording(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert w.rec_pill.isHidden()
    press(w, Qt.Key_R, "r")
    w._update_status()
    assert not w.rec_pill.isHidden() and w.rec_pill.text().startswith("Запись: 1")
    w.statusBar().showMessage("сообщение")
    assert w.rec_pill.isVisible()
    w.rec_pill.stop_requested.emit(0)
    assert not w.session.video_state().active
    w.close()


def test_refusal_goes_to_status_line(qtbot, tmp_path):
    from ptztracker.videorec.disk import DiskInfo

    w = make(qtbot, tmp_path)
    w.video.disk.snapshot = lambda: DiskInfo(False, 0, 0, "E:", "", None, "")
    press(w, Qt.Key_R, "r")
    assert not w.session.video_state().active and "не подключён" in w.statusBar().currentMessage()
    w.close()


def test_close_while_recording_can_be_cancelled(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    press(w, Qt.Key_R, "r")
    asked = []
    w._confirm_close_with_video = lambda: asked.append(1) or False
    assert w.close() is False
    assert asked == [1] and w.session.video_state().active and all(c is not None for c in w.grid.cells[:1])
    w._confirm_close_with_video = lambda: True
    assert w.close() is True


def test_close_without_recording_does_not_ask(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w._confirm_close_with_video = lambda: (_ for _ in ()).throw(AssertionError("не должен спрашивать"))
    assert w.close() is True


def test_close_finishes_file_within_two_seconds(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    press(w, Qt.Key_R, "r")
    session = w.session
    qtbot.waitUntil(lambda: session.video_state().bytes_total > 0, timeout=5000)
    w._confirm_close_with_video = lambda: True
    t0 = time.monotonic()
    w.close()
    assert time.monotonic() - t0 < 6.0
    assert session.video.wait_closed(0.1) and session.video_state().paths[0].stat().st_size > 0
