"""Окно «Горячие клавиши» (F1) и чип геймпада в верхней панели."""

from PySide6.QtCore import Qt
from test_gamepad_bridge import PadBackend, make

from ptztracker.config import CameraConfig
from ptztracker.ui.hotkeys_dialog import GAMEPAD_HELP, HotkeysDialog, fmt_remaining, keyboard_keys, pad_elements
from ptztracker.ui.main_window import _HOTKEYS


def rows(table):
    return table


def test_dialog_tables(qtbot):
    d = HotkeysDialog()
    qtbot.addWidget(d)
    assert rows(d.keys_rows) == [(k, v) for k, v in _HOTKEYS] + [("F1", "эта справка")]
    pad = rows(d.pad_rows)
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
    assert any("тачпад" in k.lower() for k, _ in rows(d.pad_rows))
    d.close()
    w.close()


def test_fmt_remaining():
    assert fmt_remaining(3 * 3600 + 10 * 60) == "≈3 ч 10 мин"
    assert fmt_remaining(45 * 60) == "≈45 мин"
    assert fmt_remaining(10) == "≈1 мин"


def test_gamepad_chip_hidden_without_device(qtbot, tmp_path):
    w, _b = make(qtbot, tmp_path, backend=PadBackend(fail=True))
    assert w.top_pads.isHidden()
    w.close()


def test_gamepad_chip_shows_number_and_battery(qtbot, tmp_path):
    cams = [CameraConfig(id="a", name="KZ_PTZ_2", ip="10.0.0.2", pad_number=None)]
    w, b = make(qtbot, tmp_path, cameras=cams)
    badge = w.top_pad_badges[0]
    assert not w.top_pads.isHidden() and not badge.isHidden()
    assert badge.battery_text() == "—" and "клетке 1" in badge.toolTip()
    w.pad._on_batt(70, False)  # SDL: 5/20/70/100 — процентов не показываем, только уровень
    assert badge.battery_text() == "средний" and "Заряд: средний" in badge.toolTip() and "%" not in badge.toolTip()
    w.pad._battery.remaining_s = lambda: 3 * 3600 + 600
    w.pad._on_batt(70, False)
    assert "≈3 ч 10 мин" in badge.toolTip()
    w.pad._on_batt(70, True)
    assert badge.battery_text() == "заряжается" and "Заряжается" in badge.toolTip()
    w.pad._on_batt(None, True)  # SDL при зарядке уровня не даёт
    assert badge.battery_text() == "заряжается" and "Заряжается" in badge.toolTip()
    w.pad._on_batt(None, None)
    assert badge.battery_text() == "—" and "аряжается" not in badge.toolTip()
    w.close()


def test_gamepad_chip_hides_on_disconnect(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w.pad._on_conn(None)
    assert w.top_pads.isHidden()
    w.close()


def test_every_gamepad_row_has_element(qtbot):
    d = HotkeysDialog()
    qtbot.addWidget(d)
    for label, _what in GAMEPAD_HELP:
        key = pad_elements(label)
        assert key and d.pad.elements_for(key), label


def test_hover_row_highlights_drawing(qtbot):
    d = HotkeysDialog()
    qtbot.addWidget(d)
    i = next(n for n, (k, _v) in enumerate(GAMEPAD_HELP) if k.startswith("Левый стик"))
    d.row_hovered(i)
    assert d.pad.highlighted == "lstick" and d.pad.animating
    assert d.rows[i].lit
    d.row_hovered(None)
    assert d.pad.highlighted is None and not d.pad.animating


def test_hover_drawing_highlights_row(qtbot):
    d = HotkeysDialog()
    qtbot.addWidget(d)
    d.pad.hovered.emit("touchpad")  # тачпад: нажатие — стоп, свайп — крупность; стоп важнее
    lit = [r for r in d.rows if r.lit]
    assert len(lit) == 1 and "СТОП КАМЕРЫ" in lit[0].what_label.text()
    d.pad.hovered.emit("dpad_left")
    lit = [r for r in d.rows if r.lit]
    assert len(lit) == 1 and lit[0].key.startswith("Крестовина")


def test_unused_key_no_highlight(qtbot):
    d = HotkeysDialog()
    qtbot.addWidget(d)
    d.tabs.setCurrentIndex(1)
    d.keyboard.hovered.emit("Q")
    assert d.keyboard.highlighted is None and not any(r.lit for r in d.rows)


def test_tabs_and_keyboard_rows(qtbot):
    d = HotkeysDialog()
    qtbot.addWidget(d)
    assert [d.tabs.tabText(i) for i in range(d.tabs.count())] == ["Геймпад", "Клавиатура", "Мышь"]
    d.tabs.setCurrentIndex(1)
    assert d.rows[-1].key_label.text() == "F1"
    space = next(r.index for r in d.rows if r.key_label.text() == "Пробел")
    d.row_hovered(space)
    assert d.keyboard.highlighted == "Space"
    d.tabs.setCurrentIndex(2)
    d.row_hovered(0)
    assert d.mouse.highlighted == "m_target"


def test_keyboard_keys_parse():
    assert keyboard_keys("1–5") == "1,2,3,4,5"
    assert keyboard_keys("← → ↑ ↓") == "Left,Right,Up,Down"
    assert keyboard_keys("+ −") == "+,−"
    assert keyboard_keys("Пробел") == "Space"


def test_drawing_does_not_move_and_no_duplicate_caption(qtbot):
    d = HotkeysDialog()
    qtbot.addWidget(d)
    d.show()
    qtbot.wait(50)
    y0, h0 = d.stack.geometry().y(), d.stack.geometry().height()
    for i in range(len(d.rows)):  # длинные и короткие пояснения
        d.row_hovered(i)
        qtbot.wait(5)
        assert (d.stack.geometry().y(), d.stack.geometry().height()) == (y0, h0)
    assert not hasattr(d, "effect_text")  # пояснение — только в списке справа


def test_drawing_hover_scrolls_row_into_view(qtbot):
    d = HotkeysDialog()
    qtbot.addWidget(d)
    d.resize(1040, 420)  # список не помещается целиком
    d.show()
    qtbot.wait(50)
    d.pad.hovered.emit("create")
    lit = next(r for r in d.rows if r.lit)
    vp = d.list_scroll.viewport()
    top = lit.mapTo(vp, lit.rect().topLeft()).y()
    assert 0 <= top and top + lit.height() <= vp.height()


def row_of(prefix):
    return next(n for n, (k, _v) in enumerate(GAMEPAD_HELP) if k.startswith(prefix))


def test_pad_rows_show_gamepad_shaped_buttons(qtbot):
    d = HotkeysDialog()
    qtbot.addWidget(d)
    assert d.rows[row_of("✕")].glyphs.keys == ["cross", "circle", "square", "triangle"]
    assert d.rows[row_of("L1")].glyphs.keys == ["l1", "r1"]
    assert d.rows[row_of("Левый стик")].glyphs.keys == ["lstick"]
    assert not d.rows[0].grab().isNull()
    d.tabs.setCurrentIndex(1)  # клавиатура — по-прежнему клавиши-подсказки
    assert d.rows[0].glyphs is None and d.rows[0].key_label.isVisible() is not None


def test_hover_one_button_lights_only_that_button(qtbot):
    d = HotkeysDialog()
    qtbot.addWidget(d)
    d.pad.hovered.emit("triangle")
    row = d.rows[row_of("✕")]
    assert row.lit and row.glyphs.lit == {"triangle"}
    assert d.pad._lit("triangle") and not d.pad._lit("cross")  # на рисунке — тоже один △
    d.row_hovered(row_of("✕"))  # навели на строку — горят все четыре
    assert row.glyphs.lit == {"cross", "circle", "square", "triangle"} and d.pad._lit("cross")


def test_stick_row_holds_move_and_press_and_touchpad_rows_are_together():
    labels = [k for k, _v in GAMEPAD_HELP]
    assert not any(k.startswith(("L3", "R3")) for k in labels)  # нажатие стика — в его строке
    left = dict(GAMEPAD_HELP)[labels[row_of("Левый стик")]]
    assert "автофокус" in left
    assert row_of("Свайп") == row_of("Тачпад") + 1
    assert any(k.startswith("PS") for k in labels)


def test_swipe_row_shows_finger_and_stick_alternates(qtbot):
    from ptztracker.ui.help_drawings import PadDrawing

    d = HotkeysDialog()
    qtbot.addWidget(d)
    d.row_hovered(row_of("Свайп"))
    assert d.pad.highlighted == "touchpad" and d.pad._anim == "swipe"
    assert PadDrawing.stick_mode(500) == "move" and PadDrawing.stick_mode(2100) == "press"
