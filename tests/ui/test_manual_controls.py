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


def test_unsupported_features_hide_rows(qtbot):
    c, _ = make(qtbot)
    rows = {
        "gain": (c.gain_auto, c.gain_slider, c.gain_value),
        "wb": (c.wb_combo, c.ctemp_slider, c.ctemp_value),
    }
    assert all(not w.isHidden() for ws in rows.values() for w in ws)
    c.set_unsupported(["gain"])
    assert all(w.isHidden() for w in rows["gain"]) and not c.wb_combo.isHidden()
    c.set_unsupported(["color_temp"])  # только температура: режим WB остаётся, ползунок температуры уходит
    assert not c.gain_slider.isHidden() and not c.wb_combo.isHidden()
    assert c.ctemp_slider.isHidden() and c.ctemp_value.isHidden()
    c.set_unsupported(["wb"])  # без баланса белого нет и температуры
    assert all(w.isHidden() for w in rows["wb"])
    c.set_unsupported([])  # камеру сменили на ту, что умеет всё
    assert all(not w.isHidden() for ws in rows.values() for w in ws)


def test_unsupported_features_survive_sync_image(qtbot):
    from ptztracker.camera.session import ImageState

    c, _ = make(qtbot)
    c.set_unsupported(["gain", "wb", "color_temp"])
    c.sync_image(ImageState(gain=0x20, wb=0, color_temp=10))
    assert c.gain_slider.isHidden() and c.wb_combo.isHidden() and c.ctemp_slider.isHidden()


def test_zoom_is_vertical_rocker_right_of_stick(qtbot):
    # место справа от стика занимает зум, как на пульте PTZ: вверх — ближе
    c, s = make(qtbot)
    sl, js = c.zoom_slider, c.joystick
    assert sl.orientation() == Qt.Vertical
    jg = js.geometry()
    sg = sl.geometry()
    sl_left = sl.mapTo(c, sg.topLeft() - sg.topLeft()).x()
    js_right = js.mapTo(c, jg.topLeft() - jg.topLeft()).x() + jg.width()
    assert sl_left > js_right
    sl.setValue(sl.maximum())
    assert s.zoom[-1] > 0


def test_zoom_column_not_taller_than_stick(qtbot):
    # Windows (крупный шрифт): колонка выше стика удлиняла правую панель, и та прокручивалась
    c, _ = make(qtbot)
    assert c._zoom_col.sizeHint().height() <= c.joystick.height()


def test_focus_slider_is_spring(qtbot):
    c, s = make(qtbot)
    c.focus_slider.setValue(-80)  # влево — ближе
    assert s.focus[-1] < 0
    c.focus_slider.setValue(60)
    assert s.focus[-1] > 0
    c.release_all()
    assert c.focus_slider.value() == 0 and s.focus[-1] == 0


def test_af_and_one_shot_share_a_row(qtbot):
    c, _ = make(qtbot)
    c.resize(320, 700)
    c.show()
    assert abs(c.af_check.geometry().center().y() - c.push_af.geometry().center().y()) <= 4
    assert abs(c.near_btn.geometry().center().y() - c.focus_slider.geometry().center().y()) <= 4


def test_one_shot_af_sits_next_to_af(qtbot):
    c, _ = make(qtbot)
    c.resize(320, 700)
    c.show()
    gap = c.af_check.geometry().left() - c.push_af.geometry().right()
    assert 0 < gap <= 24  # «Разовый», сразу за ним AF
    assert c.push_af.height() == c.near_btn.height()


def test_rows_zebra_in_each_section(qtbot):
    """Строки как в таблице: через одну — чуть светлее фон (пользователь 2026-11-05, вариант А + В)."""
    c, _ = make(qtbot)
    assert [c.rows[k].shaded for k in ("iris", "gain", "focus", "wb")] == [False, True, False, True]
    assert [c.rows[k].shaded for k in ("sens", "slow_tele")] == [True, False]
    assert not any(r.active for r in c.rows.values())


def test_row_lights_up_while_operator_changes_it(qtbot, monkeypatch):
    from ptztracker.ui import manual_controls as mc

    monkeypatch.setattr(mc, "ROW_LIT_S", 0.2)
    c, _ = make(qtbot)
    c.gain_auto.setChecked(False)
    c.gain_slider.triggerAction(c.gain_slider.SliderAction.SliderSingleStepAdd)  # как мышь или клавиша
    assert c.rows["gain"].active and not c.rows["iris"].active
    qtbot.waitUntil(lambda: not c.rows["gain"].active, timeout=1000)
    c.near_btn.pressed.emit()
    assert c.rows["focus"].active
    c.near_btn.released.emit()


def test_camera_reply_does_not_light_row(qtbot):
    """Опрос камеры меняет ползунок сам — это не «оператор крутит», строка не загорается."""
    c, _ = make(qtbot)
    c.gain_slider.setValue(3)
    c.iris_slider.setValue(80)
    assert not any(r.active for r in c.rows.values())


def test_shaded_row_has_no_dark_patches(qtbot):
    """Подписи, флажки и ползунки в светлой строке не закрывают её фон тёмными прямоугольниками."""
    from ptztracker.ui.theme import apply_dark_theme

    app = QApplication.instance()
    old_style, old_pal = app.styleSheet(), app.palette()
    apply_dark_theme(app)
    try:
        c, _ = make(qtbot)
        c.resize(360, 700)
        qtbot.wait(50)
        row = c.rows["gain"]
        img = c.grab().toImage()
        dpr = img.devicePixelRatio()
        sl = c.gain_slider
        y = sl.mapTo(c, QPoint(0, 1)).y()  # над дорожкой ползунка — там только фон
        x_row = row.mapTo(c, QPoint(row.width() - 4, 0)).x()
        x_in = sl.mapTo(c, QPoint(sl.width() * 3 // 4, 0)).x()
        px = lambda x: img.pixelColor(round(x * dpr), round(y * dpr)).name()  # noqa: E731
        assert px(x_in) == px(x_row)
    finally:
        app.setStyleSheet(old_style)
        app.setPalette(old_pal)


def test_slider_handle_not_clipped(qtbot):
    """Ручка 17 px по высоте (дорожка 3 + отступы −7) — ползунок выше неё, иначе срезаны верх и низ."""
    from ptztracker.ui.theme import apply_dark_theme

    app = QApplication.instance()
    old_style, old_pal = app.styleSheet(), app.palette()
    apply_dark_theme(app)
    try:
        c, _ = make(qtbot)
        c.resize(360, 700)
        qtbot.wait(50)
        for sl in (c.gain_slider, c.iris_slider, c.sens_slider, c.ctemp_slider, c.focus_slider):
            assert sl.height() >= 20, sl.toolTip()[:20]
        assert c.zoom_slider.width() >= 20
    finally:
        app.setStyleSheet(old_style)
        app.setPalette(old_pal)


def test_iris_and_wb_show_values(qtbot):
    """Как у Gain: у ириса — насколько открыт, у баланса белого — режим или кельвины (пользователь 2026-11-05)."""
    from ptztracker.camera import protocol as P

    c, _ = make(qtbot)
    c.iris_slider.setValue(45)
    assert c.iris_value.text() == "45 %"
    labels = {}
    for mode in (0, 1, 4, 5, P.WB_VAR):
        c.wb_combo.setCurrentIndex(c.wb_combo.findData(mode))
        labels[mode] = c.ctemp_value.text()
    assert labels[0] == "авто" and labels[1] == "память"
    assert labels[4] == "3200 K" and labels[5] == "5600 K"
    assert labels[P.WB_VAR].startswith("≈") and labels[P.WB_VAR].endswith("K")


def test_sensitivity_texts_do_not_widen_panel(qtbot):
    # Windows (шрифт шире): «Чувствительность» и «Медленнее на крупном плане» раздвигали панель до 407 px
    from PySide6.QtWidgets import QLabel

    from ptztracker.ui.main_window import SIDE_WIDTH

    c, _ = make(qtbot)
    c.slow_tele_check.setText("Медленнее на крупном плане " * 4)
    for lab in c.findChildren(QLabel):
        if lab.text() == "Чувствительность":
            lab.setText("Чувствительность " * 4)
    c.layout().invalidate()
    assert c.minimumSizeHint().width() <= SIDE_WIDTH - 16
