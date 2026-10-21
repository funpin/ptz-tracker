from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from ptztracker.ui.manual_controls import ManualControls


class FakeSession:
    def __init__(self):
        self.zoom = []
        self.focus = []

    def set_zoom_velocity(self, v):
        self.zoom.append(v)

    def set_focus_velocity(self, v):
        self.focus.append(v)

    def __getattr__(self, name):
        return lambda *a, **k: None


def make(qtbot):
    c = ManualControls()
    qtbot.addWidget(c)
    c.show()
    s = FakeSession()
    c.bind(s)
    return c, s


def test_wheel_does_not_start_zoom(qtbot):
    # Final review C2
    c, s = make(qtbot)
    sl = c.zoom_slider
    ev = QWheelEvent(QPointF(10, 5), QPointF(sl.mapToGlobal(QPoint(10, 5))), QPoint(0, 0), QPoint(0, 120),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(sl, ev)
    assert sl.value() == 0
    assert all(v == 0 for v in s.zoom)


def test_click_on_groove_springs_back(qtbot):
    c, s = make(qtbot)
    sl = c.zoom_slider
    qtbot.mousePress(sl, Qt.LeftButton, pos=QPoint(sl.width() - 5, sl.height() // 2))
    qtbot.mouseRelease(sl, Qt.LeftButton, pos=QPoint(sl.width() - 5, sl.height() // 2))
    assert sl.value() == 0
    assert s.zoom == [] or s.zoom[-1] == 0


def test_keyboard_does_not_start_zoom(qtbot):
    c, s = make(qtbot)
    sl = c.zoom_slider
    qtbot.keyClick(sl, Qt.Key_PageUp)
    assert sl.value() == 0


def test_release_all_stops_zoom_and_focus(qtbot):
    c, s = make(qtbot)
    c.zoom_slider.setValue(60)
    c.near_btn.pressed.emit()
    c.release_all()
    assert c.zoom_slider.value() == 0
    assert s.zoom[-1] == 0
    assert s.focus[-1] == 0


def test_stop_button_emits(qtbot):
    c, _ = make(qtbot)
    with qtbot.waitSignal(c.stopRequested, timeout=500):
        c.stop_btn.click()


def test_stop_button_acts_on_press_once(qtbot):
    c, _ = make(qtbot)
    c.show()
    seen = []
    c.stopRequested.connect(lambda: seen.append(1))
    center = c.stop_btn.rect().center()
    qtbot.mousePress(c.stop_btn, Qt.LeftButton, pos=center)
    assert seen == [1]  # стоп на нажатии, не ждём отпускания
    qtbot.mouseRelease(c.stop_btn, Qt.LeftButton, pos=center)
    assert seen == [1]  # и не второй раз на отпускании
    c.stop_btn.click()
    assert seen == [1, 1]  # click() — одно нажатие, один стоп


def test_stop_button_shows_esc(qtbot):
    c, _ = make(qtbot)
    assert c.stop_btn.hotkey == "Esc"
    assert c.stop_btn.focusPolicy() == Qt.NoFocus
