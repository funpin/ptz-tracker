"""Режим «камера как курсор» (план шага Б, часть 3; макет mouse-mode.html)."""

from PySide6.QtCore import QPoint, QPointF, Qt

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.preview import mouse_velocity


def test_mouse_velocity_dead_zone_and_full_tilt():
    assert mouse_velocity(0.01, -0.01) == (0.0, 0.0)  # дрожь руки у центра
    vx, vy = mouse_velocity(0.2, -0.2)  # вправо и вверх
    assert 0 < vx < 1 and vy > 0
    assert mouse_velocity(0.5, 0.5) == (1.0, -1.0)  # дальше 35 % — полная скорость, вниз — минус


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.resize(1400, 820)
    w.show()
    qtbot.waitUntil(lambda: w.preview.has_frame(), timeout=4000)
    return w


def pts(w):
    return [c for c in w.session.sim.received if c.startswith("#PTS")]


def test_tab_enters_moves_camera_and_tab_stops(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.keyClick(w, Qt.Key_Tab)
    p = w.preview
    assert w.mouse_slot == w.grid.selected and p.mouse_mode and w.mouse_hint.isVisible()
    r = p.rect()
    qtbot.mouseMove(p, QPoint(int(r.width() * 0.85), r.height() // 2))  # вправо от центра
    qtbot.waitUntil(lambda: bool(pts(w)) and pts(w)[-1] != "#PTS5050", timeout=2000)
    qtbot.keyClick(w, Qt.Key_Tab)  # выход — камера сразу стоит
    assert w.mouse_slot is None and not p.mouse_mode and not w.mouse_hint.isVisible()
    qtbot.waitUntil(lambda: pts(w)[-1] == "#PTS5050", timeout=2000)
    w.close()


def test_wheel_zooms_right_click_switches_to_iris(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.enter_mouse_mode()
    w.preview.wheelStep.emit(1)
    qtbot.waitUntil(lambda: any(c.startswith("#Z") and c != "#Z50" for c in w.session.sim.received), timeout=5000)
    assert "зум" in w.mouse_hint.text()
    w.preview.rightClicked.emit()
    assert w.mouse_wheel == "iris" and "ирис" in w.mouse_hint.text()
    before = w.controls.iris_slider.value()
    w.controls.iris_auto.setChecked(False)
    w.preview.wheelStep.emit(1)
    assert w.controls.iris_slider.value() > before or before == w.controls.iris_slider.maximum()
    w.close()


def test_esc_and_cell_change_leave_mouse_mode(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.enter_mouse_mode()
    qtbot.keyClick(w, Qt.Key_Escape)
    assert w.mouse_slot is None
    w.enter_mouse_mode()
    w.select_slot(1)
    assert w.mouse_slot is None
    w.close()


def test_double_click_on_video_enters(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    p = w.preview
    at = QPoint(10, p.height() - 10)  # вне зоны слежения
    qtbot.waitUntil(lambda: p.widget_to_frame(QPointF(at)) is not None, timeout=5000)  # до первого кадра клик не по видео
    qtbot.mouseDClick(p, Qt.LeftButton, pos=at)
    assert w.mouse_slot == w.grid.selected
    w.close()


def test_left_click_takes_person_nearest_centre(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: bool(w.session.analyzer.latest() and w.session.analyzer.latest().tracks), timeout=4000)
    w.enter_mouse_mode()
    w.preview.leftClicked.emit()
    qtbot.waitUntil(lambda: w.session.analyzer.latest().target_id is not None, timeout=2000)
    qtbot.waitUntil(lambda: w.session.follower.enabled, timeout=2000)
    w.close()
