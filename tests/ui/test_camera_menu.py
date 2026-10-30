"""Меню камеры (Options / клавиша M): поверх видео, не диалог."""

import dataclasses

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from test_gamepad_bridge import make, press

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
    w._activate(w._current_cfg())
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
