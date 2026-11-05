"""Режимы «4 камеры» и «1 камера» (пользователь 2026-11-05, макет docs/design/mockups/grid-modes.html)."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.resize(1640, 850)
    w.show()
    qtbot.waitExposed(w)
    return w


def key(w, k):
    for t in (QEvent.KeyPress, QEvent.KeyRelease):
        QApplication.sendEvent(w, QKeyEvent(t, k, Qt.NoModifier))


def test_two_cameras_start_in_grid_and_g_toggles(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert not w.single_view and all(c.isVisible() for c in w.cells)
    key(w, Qt.Key_G)
    assert w.single_view
    sel = w.grid.selected
    assert [c.isVisible() for c in w.cells] == [i == sel for i in range(4)]
    assert w.cells[sel].width() > w.grid_host.width() * 0.9  # во всю ширину
    # остальные камеры — полоской, за ними плитки служебных видов из свободных клеток
    assert w.strip.isVisible() and [it.slot for it in w.strip.items] == [1, 2, 3]
    assert [it.item.service for it in w.strip.items] == [None, "summary", "position"]
    key(w, Qt.Key_G)
    assert not w.single_view and all(c.isVisible() for c in w.cells) and not w.strip.isVisible()
    w.close()


def test_strip_click_switches_camera(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.set_single_view(True)
    w.strip.items[0].clicked.emit()
    assert w.grid.selected == 1
    assert w.cells[1].isVisible() and not w.cells[0].isVisible()
    assert [it.slot for it in w.strip.items][0] == 0
    w.close()


def test_service_tile_becomes_big(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.set_single_view(True)
    tile = next(it for it in w.strip.items if it.item.service == "summary")
    tile.clicked.emit()
    assert w.cells[2].isVisible() and not w.cells[0].isVisible()  # крупно — «Сводка» вместо камеры
    assert w.grid.selected == 0  # управление осталось у камеры
    assert [it.slot for it in w.strip.items] == [0, 1, 3]
    w.strip.items[0].clicked.emit()  # камеру — обратно крупно
    assert w.cells[0].isVisible() and not w.cells[2].isVisible()
    w.close()


def test_view_buttons_and_header_double_click(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.view_one_btn.click()
    assert w.single_view and w.view_one_btn.isChecked() and not w.view_grid_btn.isChecked()
    w.view_grid_btn.click()
    assert not w.single_view
    w.cells[1].viewToggleRequested.emit(1)  # двойной клик по шапке клетки
    assert w.single_view and w.grid.selected == 1
    w.close()


def test_one_camera_opens_single(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.set_cell_camera(1, None)
    w._auto_view()  # то же, что при запуске с одной камерой
    assert w.single_view and [it.item.service for it in w.strip.items] == ["summary", "position", "graphs"]
    w.close()
