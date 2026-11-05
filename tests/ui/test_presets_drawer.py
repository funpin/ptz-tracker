"""Выдвижная панель пресетов: строка выбранной камеры, карточки со снимками, тянется вверх за другими камерами."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

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
    assert card.last
    head = d.row_widgets[0].number
    assert head.text() == "2" and "Зал" in head.toolTip()  # номер клетки, имя — в подсказке
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



def test_no_scrollbar_wheel_scrolls(qtbot):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QWheelEvent

    d = PresetsDrawer()
    qtbot.addWidget(d)
    d.set_rows(rows())
    d.resize(700, 200)
    d.show()
    row = d.row_widgets[0]
    assert row.scroll.horizontalScrollBar().isHidden() or \
        row.scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    bar = row.scroll.horizontalScrollBar()
    assert bar.maximum() > 0 and row.more_right.isEnabled()
    ev = QWheelEvent(QPointF(50, 30), QPointF(50, 30), QPoint(0, 0), QPoint(0, -240), Qt.NoButton,
                     Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(row.scroll.viewport(), ev)
    assert bar.value() > 0


def test_no_rename_in_menu(qtbot):
    d = PresetsDrawer()
    qtbot.addWidget(d)
    assert not hasattr(d, "renameRequested")


def test_opening_presets_grows_window_not_cameras(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.resize(1640, 850)
    qtbot.wait(100)
    h0, cell0 = w.height(), w.grid_host.height()
    w.presets_btn.click()
    qtbot.waitUntil(lambda: w.height() > h0 + 50, timeout=2000)
    qtbot.wait(100)
    assert abs(w.grid_host.height() - cell0) <= 4
    w.presets_drawer.set_rows_shown(2)
    qtbot.wait(100)
    assert abs(w.grid_host.height() - cell0) <= 4
    w.presets_btn.click()
    qtbot.waitUntil(lambda: abs(w.height() - h0) <= 2, timeout=2000)
    w.close()


def test_whole_cards_only(qtbot):
    d = PresetsDrawer()
    qtbot.addWidget(d)
    d.set_rows(rows())
    for width in (700, 913, 1280):
        d.resize(width, 200)
        d.show()
        qtbot.wait(20)
        row = d.row_widgets[0]
        vw = row.scroll.width()
        from ptztracker.ui.presets_drawer import CARD_W

        assert (vw + row.CARD_GAP) % (CARD_W + row.CARD_GAP) == 0, (width, vw)


def test_preset_speed_slider(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.presets_btn.click()
    qtbot.waitUntil(lambda: w.session.image.preset_speed is not None, timeout=4000)
    sl = w.presets_drawer.speed_slider
    qtbot.waitUntil(lambda: sl.value() == w.session.image.preset_speed, timeout=2000)  # окно обновляется раз в 0,5 с
    sl.setValue(30)
    sl.sliderReleased.emit()
    qtbot.waitUntil(lambda: "#UPVS999" in w.session.sim.received, timeout=2000)
    w.close()


def test_card_strip_has_no_dark_box(qtbot):
    """Между карточками и вокруг них — фон строки, а не тёмный прямоугольник области прокрутки."""
    from ptztracker.ui.theme import apply_dark_theme

    app = QApplication.instance()
    old_style, old_pal = app.styleSheet(), app.palette()
    apply_dark_theme(app)
    try:
        d = PresetsDrawer()
        qtbot.addWidget(d)
        d.resize(1500, 200)
        d.set_rows(rows())
        d.show()
        qtbot.wait(100)
        row = d.row_widgets[0]
        img = d.grab().toImage()
        c1, c2 = row.cards[0], row.cards[1]
        y = c1.mapTo(d, QPoint(0, c1.height() // 2)).y()
        gap_x = (c1.mapTo(d, QPoint(c1.width(), 0)).x() + c2.mapTo(d, QPoint(0, 0)).x()) // 2
        head_x = row.mapTo(d, QPoint(2, 0)).x()  # фон строки у её левого края
        dpr = img.devicePixelRatio()
        px = lambda x: img.pixelColor(round(x * dpr), round(y * dpr)).name()  # noqa: E731
        assert px(gap_x) == px(head_x)
    finally:
        app.setStyleSheet(old_style)
        app.setPalette(old_pal)


def test_badge_shows_gamepad_button():
    from ptztracker.ui.presets_drawer import badge_text

    assert [badge_text(n) for n in (1, 2, 3, 4, 5)] == ["1 ×", "2 ○", "3 □", "4 △", "5"]
