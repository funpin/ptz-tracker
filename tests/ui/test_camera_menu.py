"""Меню камеры (Options / клавиша M): поверх видео, не диалог."""

import dataclasses

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from test_gamepad_bridge import make, press, stick

from ptztracker.camera import protocol as P
from ptztracker.camera.session import ImageState
from ptztracker.ui import camera_menu as cm
from ptztracker.ui.camera_menu import CameraMenu
from ptztracker.ui.manual_controls import ManualControls


class FakeSession:
    """Сессия с методами фокуса и ириса, но без gain/WB."""

    def __init__(self):
        self.calls = []
        self.image = ImageState()

    def set_focus_velocity(self, v):
        self.calls.append(("focus_v", v))

    def set_focus_auto(self, on):
        self.calls.append(("focus_auto", on))

    def set_iris_auto(self, on):
        self.calls.append(("iris_auto", on))


class FullSession(FakeSession):
    def set_gain(self, code):
        self.calls.append(("gain", code))

    def set_wb(self, mode):
        self.calls.append(("wb", mode))

    def set_color_temp(self, i):
        self.calls.append(("ctemp", i))


def build(qtbot, session):
    c = ManualControls()
    qtbot.addWidget(c)
    c.bind(session)
    m = CameraMenu(lambda: session, c)
    qtbot.addWidget(m)
    return m, c


def test_focus_manual_near_far(qtbot):
    s = FakeSession()
    m, c = build(qtbot, s)
    m.open()
    m.accept()  # Фокус: Авто → Ручной
    assert ("focus_auto", False) in s.calls and not c.af_check.isChecked()
    m.nav(-1, 0)
    m.nav(0, 0)
    m.nav(1, 0)
    m.nav(0, 0)
    assert [x for x in s.calls if x[0] == "focus_v"] == [("focus_v", -0.3), ("focus_v", 0.0),
                                                         ("focus_v", 0.3), ("focus_v", 0.0)]


def test_focus_auto_arrows_do_nothing(qtbot):
    s = FakeSession()
    m, _ = build(qtbot, s)
    m.open()
    m.nav(1, 0)
    assert not [x for x in s.calls if x[0] == "focus_v"]


def test_close_stops_moving_focus(qtbot):
    s = FakeSession()
    m, _ = build(qtbot, s)
    m.open()
    m.accept()
    m.nav(1, 0)
    m.close_menu()
    assert s.calls[-1] == ("focus_v", 0.0) and not m.is_open and not m.isVisible()


def test_wb_gain_disabled_without_session_methods(qtbot):
    s = FakeSession()
    s.image = ImageState(gain=0x20, wb=0)
    m, _ = build(qtbot, s)
    m.open()
    assert not m._enabled("wb") and not m._enabled("gain")
    for _ in range(4):
        m.nav(0, 1)
    assert m._cursor == "iris"  # неактивные пропускаются
    m.nav(0, 1)
    assert m._cursor == "iris"
    m._cursor = "gain"
    m.accept()
    assert not [x for x in s.calls if x[0] in ("gain", "wb")]


def test_gain_and_wb_through_session(qtbot):
    s = FullSession()
    s.image = ImageState(gain=P.GAIN_AUTO, wb=0)
    m, c = build(qtbot, s)
    m.open()
    m.nav(0, 1)
    assert m._cursor == "wb"
    m.nav(1, 0)
    assert s.calls[-1] == ("wb", 1)
    m.nav(0, 1)  # ирис
    m.nav(0, 1)  # gain
    assert m._cursor == "gain"
    m.accept()
    assert s.calls[-1] == ("gain", P.GAIN_CODES[c.gain_slider.value()])
    m.accept()  # снова авто (по только что выбранному значению)
    assert s.calls[-1] == ("gain", P.GAIN_AUTO)


def test_wb_var_shows_color_temp(qtbot):
    s = FullSession()
    s.image = ImageState(wb=P.WB_VAR, color_temp=10, gain=0x20)
    m, _ = build(qtbot, s)
    m.open()
    assert "ctemp" in m.visible_keys()
    m.nav(0, 1)
    m.nav(0, 1)
    assert m._cursor == "ctemp"
    m.nav(1, 0)
    assert s.calls[-1] == ("ctemp", 10 + cm.CTEMP_STEP)
    s.image = dataclasses.replace(s.image, wb=0)
    qtbot.wait(300)
    assert "ctemp" not in m.visible_keys()


def test_menu_autoclose(qtbot, monkeypatch):
    monkeypatch.setattr(cm, "AUTOCLOSE_S", 0.15)
    m, _ = build(qtbot, FakeSession())
    m.open()
    assert m.is_open
    qtbot.waitUntil(lambda: not m.is_open, timeout=1500)


def test_menu_nav_restarts_autoclose(qtbot, monkeypatch):
    monkeypatch.setattr(cm, "AUTOCLOSE_S", 0.4)
    m, _ = build(qtbot, FakeSession())
    m.open()
    for _ in range(4):
        qtbot.wait(150)
        m.nav(0, 1)
    assert m.is_open
    qtbot.waitUntil(lambda: not m.is_open, timeout=2000)


def test_mouse_click_on_row(qtbot):
    s = FakeSession()
    m, _ = build(qtbot, s)
    m.open()
    m.click_item("iris")
    assert ("iris_auto", False) in s.calls


def key(w, k, press_=True):
    ev = QKeyEvent(QEvent.KeyPress if press_ else QEvent.KeyRelease, k, Qt.NoModifier)
    QApplication.sendEvent(w, ev)


def test_options_opens_menu_blocks_presets(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    sim = w.session.sim
    press(qtbot, b, "options")
    qtbot.waitUntil(lambda: w.camera_menu.is_open, timeout=1000)
    n = len(list(sim.received))
    press(qtbot, b, "dpad_left")  # в меню — значение, не пресет
    press(qtbot, b, "square")
    qtbot.wait(150)
    assert not [c for c in list(sim.received)[n:] if c.startswith("#R") or c.startswith("#M")]
    press(qtbot, b, "circle")
    qtbot.waitUntil(lambda: not w.camera_menu.is_open, timeout=1000)
    w.close()


def test_menu_lightbar_follows_open(qtbot, tmp_path):
    from ptztracker.gamepad.feedback import PadStatus

    w, b = make(qtbot, tmp_path)
    seen = []
    orig = w.pad.set_status
    w.pad.set_status = lambda s, t=None: (seen.append(s), orig(s, t))
    w.camera_menu.open()
    qtbot.waitUntil(lambda: any(isinstance(s, PadStatus) and s.menu_open for s in seen), timeout=1000)
    w.camera_menu.close_menu()
    qtbot.waitUntil(lambda: not seen[-1].menu_open, timeout=1000)
    w.close()


def test_menu_closes_on_camera_switch_and_stop(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w.camera_menu.open()
    w.set_cell_camera(w.grid.selected, w._current_cfg().id)
    assert not w.camera_menu.is_open
    w.camera_menu.open()
    w.emergency_stop()
    assert not w.camera_menu.is_open
    w.camera_menu.open()
    press(qtbot, b, "touchpad")  # тачпад = аварийный стоп
    qtbot.waitUntil(lambda: not w.camera_menu.is_open, timeout=1000)
    w.close()


def test_keyboard_m_and_arrows(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    key(w, Qt.Key_M)
    assert w.camera_menu.is_open
    sim = w.session.sim
    n = len(list(sim.received))
    key(w, Qt.Key_Down)
    key(w, Qt.Key_Down, False)
    assert w.camera_menu._cursor != "focus"
    assert not w._held and not [c for c in list(sim.received)[n:] if c.startswith("#PTS") and c != "#PTS5050"]
    key(w, Qt.Key_Escape)
    assert not w.camera_menu.is_open
    key(w, Qt.Key_M)
    key(w, Qt.Key_M)
    assert not w.camera_menu.is_open
    w.close()


def test_menu_fits_window(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w.resize(1640, 850)
    w.camera_menu.open()
    qtbot.wait(100)
    m, vf = w.camera_menu, w.video_frame
    assert m.parent() is vf
    assert vf.rect().contains(m.geometry())
    w.close()


def focus_vs(s):
    return [x[1] for x in s.calls if x[0] == "focus_v"]


def manual_focus(m):
    m.open()
    m.accept()
    m.nav(1, 0)


def test_timeout_while_holding_stops_focus_and_closes(qtbot, monkeypatch):
    monkeypatch.setattr(cm, "AUTOCLOSE_S", 0.2)
    s = FakeSession()
    m, _ = build(qtbot, s)
    manual_focus(m)
    qtbot.waitUntil(lambda: not m.is_open, timeout=1500)
    assert focus_vs(s)[-1] == 0.0


def test_long_hold_stops_after_limit(qtbot, monkeypatch):
    monkeypatch.setattr(cm, "FOCUS_MAX_S", 0.2)
    s = FakeSession()
    m, _ = build(qtbot, s)
    manual_focus(m)
    qtbot.waitUntil(lambda: focus_vs(s)[-1] == 0.0, timeout=1500)
    assert m.is_open


def test_disconnect_while_holding_stops_focus(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w.camera_menu.open()
    w.camera_menu.accept()
    b.pad = stick(buttons={"dpad_right"})
    qtbot.waitUntil(lambda: w.camera_menu._focus_moving, timeout=1000)
    sent = []
    w.session.set_focus_velocity = lambda v: sent.append(v)
    b.pad = None
    qtbot.waitUntil(lambda: bool(sent) and sent[-1] == 0.0, timeout=1000)
    w.close()


def test_deactivation_while_holding_stops_focus(qtbot, tmp_path, monkeypatch):
    w, b = make(qtbot, tmp_path)
    w.camera_menu.open()
    w.camera_menu.accept()
    w.camera_menu.nav(1, 0)
    assert w.camera_menu._focus_moving
    sent = []
    w.session.set_focus_velocity = lambda v: sent.append(v)
    monkeypatch.setattr(w, "isActiveWindow", lambda: False)
    w.changeEvent(QEvent(QEvent.ActivationChange))
    assert sent and sent[-1] == 0.0
    w.close()


class NoGainSession(FullSession):
    def __init__(self, unsupported):
        super().__init__()
        self.unsupported = set(unsupported)

    def supports(self, feature):
        return feature not in self.unsupported


def test_menu_items_follow_session_supports(qtbot):
    s = NoGainSession({"gain", "color_temp"})
    s.image = ImageState(gain=0x20, wb=P.WB_VAR, color_temp=10)
    m, _ = build(qtbot, s)
    m.open()
    assert m._enabled("wb") and m._enabled("iris")
    assert not m._enabled("gain") and not m._enabled("ctemp")
    m._cursor = "gain"
    m.accept()
    assert not [x for x in s.calls if x[0] == "gain"]
    s.unsupported = {"wb"}  # без баланса белого температура тоже недоступна
    assert not m._enabled("wb") and not m._enabled("ctemp") and m._enabled("gain")


class RareSession(FullSession):
    def set_shutter(self, code):
        self.calls.append(("shutter", code))

    def set_synchro(self, step):
        self.calls.append(("synchro", step))

    def set_pedestal(self, level):
        self.calls.append(("pedestal", level))

    def set_detail(self, mode):
        self.calls.append(("detail", mode))


def test_shutter_steps_and_synchro_row(qtbot):
    s = RareSession()
    s.image = dataclasses.replace(ImageState(), shutter=0, synchro=1, pedestal=0, detail=1)
    m, _ = build(qtbot, s)
    m.open()
    assert "shutter" in m.visible_keys() and "synchro" not in m.visible_keys()
    m.click_item("shutter")  # клик — следующая ступень
    assert s.calls[-1] == ("shutter", 3)
    assert m.values()["shutter"].startswith("1/120")  # сеть 50 Гц
    s.image = dataclasses.replace(s.image, shutter=P.SHUTTER_SYNCHRO)
    m._local.clear()
    m._render()
    assert "synchro" in m.visible_keys() and "Гц" in m.values()["synchro"]
    m._cursor = "synchro"
    m.nav(1, 0)
    assert s.calls[-1][0] == "synchro" and s.calls[-1][1] > 1


def test_pedestal_and_detail_arrows(qtbot):
    s = RareSession()
    s.image = dataclasses.replace(ImageState(), shutter=0, pedestal=0, detail=1)
    m, _ = build(qtbot, s)
    m.open()
    m._cursor = "pedestal"
    m.nav(-1, 0)
    assert s.calls[-1] == ("pedestal", -1)
    m._cursor = "detail"
    m.nav(1, 0)
    assert s.calls[-1] == ("detail", 2) and m.values()["detail"] == "Высокая"


def test_rare_rows_hidden_without_support(qtbot):
    s = FullSession()  # старая сессия без затвора и резкости
    m, _ = build(qtbot, s)
    m.open()
    assert not {"shutter", "pedestal", "detail"} & set(k for k in m.visible_keys() if m._enabled(k))


def test_rows_explain_why(qtbot):
    s = RareSession()
    s.image = dataclasses.replace(ImageState(), shutter=0, pedestal=0, detail=1, gain=8, wb=0)
    m, _ = build(qtbot, s)
    m.open()
    for k in m.visible_keys():
        assert len(cm.WHY[k]) > 15 and m._rows[k].toolTip() == cm.WHY[k]
    m._cursor = "shutter"
    m._render()
    assert m.hint.text() == cm.WHY["shutter"] and "мерцан" in m.hint.text()


class OsdSession(RareSession):
    def set_osd(self, on):
        self.calls.append(("osd", on))

    def osd_key(self, key):
        self.calls.append(("osd_key", key))


def test_quick_settings_title_and_osd_item(qtbot):
    s = OsdSession()
    s.image = dataclasses.replace(ImageState(), shutter=0, pedestal=0, detail=1)
    m, _ = build(qtbot, s)
    m.open()
    assert m.title_label.text().startswith("Быстрые настройки")
    assert m.visible_keys()[-1] == "osd" and "Panasonic" in m._rows["osd"].title.text()
    m.click_item("osd")
    assert ("osd", True) in s.calls and m.osd_mode
    assert m.title_label.text().startswith("Меню Panasonic")
    m.nav(0, 1)
    m.nav(0, -1)
    m.nav(1, 0)
    m.nav(-1, 0)
    m.accept()
    assert [c[1] for c in s.calls if c[0] == "osd_key"] == ["down", "up", "enter", "back", "enter"]
    m.close_menu()
    assert s.calls[-1] == ("osd", False) and not m.osd_mode


def test_osd_on_air_asks_first(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    s = OsdSession()
    s.image = dataclasses.replace(ImageState(), shutter=0, pedestal=0, detail=1)
    m, _ = build(qtbot, s)
    m.on_air = lambda: True
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or QMessageBox.No)
    m.open()
    m.click_item("osd")
    assert asked and ("osd", True) not in s.calls and not m.osd_mode


def test_quick_menu_lights_panel_row(qtbot):
    """Крутите gain из быстрых настроек (M / геймпад) — на панели загорается строка Gain."""
    s = FullSession()
    s.image = ImageState(gain=P.GAIN_AUTO, wb=0)
    m, c = build(qtbot, s)
    m.open()
    m.nav(0, 1)  # wb
    m.nav(1, 0)
    assert c.rows["wb"].active
    m.nav(0, 1)
    m.nav(0, 1)  # gain
    m.accept()
    assert c.rows["gain"].active


# --- камера в Full Auto, курсор, текст целиком (жалоба пользователя 2026-11-06) ---------------------


def test_cursor_stays_near_when_row_disappears(qtbot):
    s = FullSession()
    s.image = ImageState(wb=P.WB_VAR, color_temp=10, gain=0x20)
    m, _ = build(qtbot, s)
    m.open()
    m.nav(0, 1)
    m.nav(0, 1)
    assert m._cursor == "ctemp"
    s.image = dataclasses.replace(s.image, wb=0)  # строка «Цвет. температура» пропала
    qtbot.wait(300)
    assert m._cursor == "wb"  # не прыгает на первый пункт


def test_refused_by_camera_says_full_auto(qtbot):
    s = FullSession()
    s.image = ImageState(wb=0, gain=0x20)
    s.refused_recently = lambda: True
    m, _ = build(qtbot, s)
    m.open()
    m.nav(0, 1)
    assert "Full Auto" in m.hint.text() and cm.C.LOST in m.hint.styleSheet()
    s.refused_recently = lambda: False
    m._render()
    assert "Full Auto" not in m.hint.text()


def test_title_and_hint_fit(qtbot):
    m, _ = build(qtbot, FullSession())
    m.open()
    assert m.title_label.text() == "Быстрые настройки" and "закрыть" in m.keys_label.text()
    m.nav(0, 1)  # баланс белого: самое длинное пояснение
    m.show()
    qtbot.wait(50)
    assert m.hint.height() >= m.hint.heightForWidth(m.hint.width())


def test_image_refusal_detection():
    from ptztracker.camera.session import is_image_refusal

    assert is_image_refusal("OAW:1", "ER3:OAW") and is_image_refusal("OSH:3", "er3:OSH")
    assert is_image_refusal("#AXI95B", "eR3:AXI") and is_image_refusal("#D30", "eR3:")  # ирис
    assert not is_image_refusal("OAW:1", "OAW:1") and not is_image_refusal("#PTS5050", "ER3:PTS")
    assert not is_image_refusal("OAW:1", "ER1:OAW")
