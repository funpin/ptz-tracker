"""Выдвижная панель пресетов: строка выбранной камеры, карточки со снимками, тянется вверх за другими камерами."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QMessageBox

from ptztracker.camera.preset_thumbs import PresetThumb
from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.presets_drawer import SLOTS, DrawerRow, PresetsDrawer


def rows():
    return [DrawerRow(1, "Зал", "pvw", {2: PresetThumb(None, "Общий", 10.0)}, last=2),
            DrawerRow(0, "Сцена", "pgm", {}, last=None)]


def test_rows_and_cards(qtbot):
    d = PresetsDrawer()
    qtbot.addWidget(d)
    d.set_rows(rows())
    d.show()
    assert len(d.row_widgets) == 2 and len(d.row_widgets[0].cards) == SLOTS
    assert d.rows_shown == 1 and d.row_widgets[1].isHidden()
    card = d.row_widgets[0].cards[1]
    assert card.name == "Общий" and card.last
    assert not d.row_widgets[0].cards[0].last
    d.set_rows_shown(5)
    assert d.rows_shown == 2 and not d.row_widgets[1].isHidden()


def test_click_and_shift_click(qtbot):
    d = PresetsDrawer()
    qtbot.addWidget(d)
    d.set_rows(rows())
    d.show()
    got = []
    d.recallRequested.connect(lambda s, n: got.append(("recall", s, n)))
    d.storeRequested.connect(lambda s, n: got.append(("store", s, n)))
    card = d.row_widgets[0].cards[2]
    qtbot.mouseClick(card, Qt.LeftButton)
    qtbot.mouseClick(card, Qt.LeftButton, Qt.ShiftModifier)
    assert got == [("recall", 1, 3), ("store", 1, 3)]


def test_grip_drag_shows_more_rows(qtbot):
    d = PresetsDrawer()
    qtbot.addWidget(d)
    d.set_rows(rows())
    d.show()
    g = d.grip
    qtbot.mousePress(g, Qt.LeftButton, pos=QPoint(10, 3))
    qtbot.mouseMove(g, QPoint(10, 3 - d.ROW_H - 5))
    qtbot.mouseRelease(g, Qt.LeftButton, pos=QPoint(10, 3 - d.ROW_H - 5))
    assert d.rows_shown == 2


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    return w


def test_toggle_by_button_and_p(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert w.presets_drawer.isHidden()
    w.presets_btn.click()
    assert w.presets_drawer.isVisible() and w.presets_btn.isChecked()
    qtbot.keyClick(w, Qt.Key_P)
    assert w.presets_drawer.isHidden() and not w.presets_btn.isChecked()
    w.close()


def test_selected_camera_first(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.presets_btn.click()
    w.select_slot(1)
    assert [r.slot for r in w.presets_drawer.rows] == [1, 0]
    w.close()


def test_recall_other_camera_selects_it(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.presets_btn.click()
    w.recall_preset(1, 4)
    assert w.grid.selected == 1
    qtbot.waitUntil(lambda: "#R03" in w.grid.cells[1].session.sim.received, timeout=2000)
    assert w.presets_drawer.rows[0].last == 4
    w.close()


def test_store_saves_thumbnail(qtbot, tmp_path, monkeypatch):
    w = make(qtbot, tmp_path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w.presets_btn.click()
    qtbot.waitUntil(lambda: w.session.frames.latest() is not None, timeout=4000)
    w.store_preset(0, 2)
    qtbot.waitUntil(lambda: "#M01" in w.session.sim.received, timeout=2000)
    qtbot.waitUntil(lambda: 2 in w.thumbs.load(w.session.cfg.id), timeout=3000)
    assert w.thumbs.load(w.session.cfg.id)[2].image is not None
    assert w.presets_drawer.rows[0].thumbs[2].image is not None
    w.close()


def test_recall_without_thumb_captures_when_camera_stops(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.recall_preset(0, 5)
    qtbot.waitUntil(lambda: 5 in w.thumbs.load(w.session.cfg.id), timeout=8000)
    w.close()

