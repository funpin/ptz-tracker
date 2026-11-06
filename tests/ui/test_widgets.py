import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from ptztracker.ui.theme import C, apply_dark_theme, qcolor
from ptztracker.ui.widgets import (
    BrandMark,
    Chip,
    KeyButton,
    PadBadge,
    SectionHeader,
    StateTag,
    TrackButton,
    VideoFrame,
    paint_brackets,
)

KINDS = ("off", "on", "lost", "remote", "swing")


@pytest.fixture(autouse=True)
def _theme(qapp):
    apply_dark_theme(QApplication.instance())


def test_keybutton_reserves_room_for_keycap(qtbot):
    kb = KeyButton("Рост", "4")
    assert kb.hotkey == "4"
    assert kb.sizeHint().width() > QPushButton("Рост").sizeHint().width()


def test_chip_kind_switch(qtbot):
    c = Chip("a")
    assert c.kind() == "neutral"
    c.set("x", "pgm")
    assert c.kind() == "pgm" and c.text() == "x"


def test_section_header(qtbot):
    h = SectionHeader("Кадр", "подсказка")
    qtbot.addWidget(h)
    assert {lb.text() for lb in h.findChildren(QLabel)} == {"Кадр", "подсказка"}


def test_track_button_texts_and_tooltip(qtbot):
    t = TrackButton()
    qtbot.addWidget(t)
    expected = {"off": "TRACK OFF", "on": "TRACK ON", "lost": "ИЩУ ЦЕЛЬ", "remote": "TRACK OFF",
                "swing": "TRACK OFF"}
    for k in KINDS:
        t.set_view(k, "СЛОВО", "очень длинная причина " * 10, k == "swing")
        assert t.text() == expected[k] and t.kind() == k
        assert "очень длинная причина" in t.toolTip() and "Пробел" in t.toolTip()
    t.set_why("Сначала кликните по человеку на видео.")
    assert "кликните" in t.toolTip()


def test_track_button_action_only_for_swing(qtbot):
    t = TrackButton()
    qtbot.addWidget(t)
    for k in KINDS:
        t.set_view(k, "w", "p", k == "swing")
        assert t.action_btn.isHidden() == (k != "swing")


def test_track_button_action_signal(qtbot):
    t = TrackButton()
    qtbot.addWidget(t)
    qtbot.addWidget(t.action_btn)
    t.set_view("swing", "w", "p", True)
    with qtbot.waitSignal(t.actionClicked, timeout=500):
        t.action_btn.click()


def test_track_button_click_toggles(qtbot):
    t = TrackButton()
    qtbot.addWidget(t)
    t.click()
    assert t.isChecked()


def test_video_frame_overlays_placed(qtbot):
    f = VideoFrame(QLabel("v"))
    qtbot.addWidget(f)
    top, bottom = QLabel("top"), QLabel("bottom")
    f.add_overlay(top, "top-left")
    f.add_overlay(bottom, "bottom-left")
    f.resize(800, 450)
    f.show()
    qtbot.wait(20)
    assert top.parent() is f and top.y() < 100 and bottom.y() > 300


def test_video_frame_tally_tag(qtbot):
    f = VideoFrame(QLabel("v"))
    qtbot.addWidget(f)
    f.resize(400, 300)
    f.show()
    assert f.tracking_tag.isHidden()
    assert f.onair_tag.isHidden() and f.tally() is None
    f.set_tally("pgm")
    assert f.onair_tag.text() == "ЭФИР · PGM" and f.onair_tag.isVisible()
    f.set_tally("pvw")
    assert f.onair_tag.text() == "ПРЕВЬЮ · PVW"
    f.set_tally(None)
    assert f.onair_tag.isHidden()


def test_video_frame_tracking_tag(qtbot):
    f = VideoFrame(QLabel("v"))
    qtbot.addWidget(f)
    f.resize(400, 300)
    f.show()
    f.set_tracking_tag("СЛЕЖЕНИЕ", "#3D8BFF")
    assert f.tracking_tag.text() == "СЛЕЖЕНИЕ"
    assert f.tracking_tag.x() < f.width() // 2
    f.set_tally("pgm")
    assert f.onair_tag.x() > f.width() // 2


def test_icon_recolors():
    from ptztracker.ui.icons import icon

    for name in ("video", "adjustments", "screen", "chip", "atem", "history", "robot", "menu", "track",
                 "power", "user-x", "skeleton"):
        assert not icon(name, "#ff0000").pixmap(18, 18).isNull(), name


def test_status_pill_compact_hides_text(qtbot):
    from ptztracker.ui.widgets import StatusPill

    p = StatusPill("video")
    qtbot.addWidget(p)
    p.set("1280×720 · 25", "ok", "Видео: MJPEG")
    assert p.text() == "1280×720 · 25" and p.toolTip() == "Видео: MJPEG" and p.kind() == "ok"
    wide = p.sizeHint().width()
    p.set_compact(True)
    assert p.text() == "" and p.full_text == "1280×720 · 25" and p.sizeHint().width() < wide
    p.set_compact(False)
    assert p.text() == "1280×720 · 25"


def test_power_overlay_round_and_opaque(qtbot):
    from PySide6.QtCore import Qt

    from ptztracker.ui.widgets import PowerOverlay

    o = PowerOverlay()
    qtbot.addWidget(o)
    o.resize(800, 450)
    o.set_view("standby", "KZ")
    o.show()
    qtbot.wait(20)
    assert o.power_btn.width() == o.power_btn.height() == PowerOverlay.BTN
    assert o.testAttribute(Qt.WA_StyledBackground)


def test_section_header_hint_does_not_widen_panel(qtbot):
    # Windows: длинная подсказка в заголовке раздвигала правую панель шире окна, пресет «9» обрезался
    from ptztracker.ui.widgets import SectionHeader

    h = SectionHeader("Камера", "движение выключает слежение " * 5)
    qtbot.addWidget(h)
    assert h.minimumSizeHint().width() < 120
    assert "движение выключает слежение" in h.hint_label.toolTip()


def test_track_on_text_is_dark(qtbot):
    t = TrackButton()
    qtbot.addWidget(t)
    t.set_view("on", "", "", False)
    assert f"color: {C.BG}" in t.styleSheet() and f"background: {C.LIME}" in t.styleSheet()
    t.set_view("lost", "", "", False)
    assert f"color: {C.BG}" in t.styleSheet() and f"background: {C.LOST}" in t.styleSheet()


def test_track_cams_fit_and_click(qtbot):
    t = TrackButton()
    qtbot.addWidget(t)
    t.set_view("on", "", "", False)
    w1 = t.sizeHint().width()
    t.set_cams([(1, "on", True), (2, "off", False), (3, "lost", False), (4, "on", False)])
    t.resize(t.sizeHint())
    t.show()
    assert t.sizeHint().width() >= w1 + 4 * 22
    r = t.cam_rect(3)
    assert t.rect().contains(r.toRect())
    with qtbot.waitSignal(t.camToggled, timeout=500) as sig:
        qtbot.mouseClick(t, Qt.LeftButton, pos=r.center().toPoint())
    assert sig.args == [3]


def test_track_cam_click_does_not_toggle_selected(qtbot):
    t = TrackButton()
    qtbot.addWidget(t)
    t.set_cams([(1, "on", True), (2, "off", False)])
    t.resize(t.sizeHint())
    t.show()
    checked = t.isChecked()
    qtbot.mouseClick(t, Qt.LeftButton, pos=t.cam_rect(2).center().toPoint())
    assert t.isChecked() == checked


def test_pad_badge_values(qtbot):
    widths = set()
    for args, text in (((1, 0, False), "почти пусто"), ((2, 100, False), "полный"), ((3, None, False), "—"),
                       ((4, 46, True), "заряжается"), ((5, 70, False), "средний"), ((6, 20, False), "мало")):
        b = PadBadge()
        qtbot.addWidget(b)
        b.set(*args)
        b.grab()
        assert b.battery_text() == text
        widths.add(b.sizeHint().width())
    assert len(widths) == 1


def test_pad_badge_fill_color():
    assert PadBadge.fill_color(5, False) == C.FAULT
    assert PadBadge.fill_color(18, False) == C.LOST
    assert PadBadge.fill_color(46, True) == C.LIME
    assert PadBadge.fill_color(83, False) == C.TEXT
    assert PadBadge.text_color(83) == C.BG and PadBadge.text_color(18) == C.TEXT


def test_state_tag_kinds(qtbot):
    texts = {}
    for k in ("on", "lost", "off", "standby", "no_analysis", "pgm", "pvw", "sim"):
        t = StateTag(k)
        qtbot.addWidget(t)
        texts[k] = t.text()
    assert texts["on"] == "ВЕДУ" and texts["lost"] == "ИЩУ" and texts["no_analysis"] == "БЕЗ АНАЛИЗА"
    assert len(set(texts.values())) == len(texts)
    assert f"background: {C.PGM}" in StateTag("pgm").styleSheet()
    assert "background: transparent" in StateTag("pvw").styleSheet()


def test_brackets_only_at_corners():
    img = QImage(100, 100, QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    paint_brackets(p, QRectF(10, 10, 80, 80), qcolor(C.LIME), 3)
    p.end()
    lime = qcolor(C.LIME).rgb()
    assert img.pixel(50, 10) != lime  # середина верхней стороны пустая
    assert img.pixel(14, 10) == lime  # левый верхний угол
    assert img.pixel(89, 86) == lime  # правый нижний угол
    assert img.pixel(89, 14) != lime  # правого верхнего угла нет: две скобки, как в знаке


def test_target_brackets_mirror_the_zone():
    img = QImage(100, 100, QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    paint_brackets(p, QRectF(10, 10, 80, 80), qcolor(C.LIME), 3, corners=("tr", "bl"))
    p.end()
    lime = qcolor(C.LIME).rgb()
    assert img.pixel(86, 10) == lime and img.pixel(10, 86) == lime  # «ВЕДУ»: правый верхний и левый нижний
    assert img.pixel(14, 10) != lime and img.pixel(89, 86) != lime  # углы знака — у зоны слежения


def test_brand_mark_paints(qtbot):
    m = BrandMark(24)
    qtbot.addWidget(m)
    assert m.sizeHint().width() == 24
    assert not m.grab().isNull()


def test_battery_time_text():
    from ptztracker.ui.widgets import battery_time_text

    assert battery_time_text(6 * 3600) == "6h"
    assert battery_time_text(5400) == "1.5h"
    assert battery_time_text(1800) == "30m"
    assert battery_time_text(50) == "1m"
    assert battery_time_text(None) == "—"


def test_pad_badge_shows_level_not_numbers(qtbot):
    b = PadBadge(framed=False)
    qtbot.addWidget(b)
    b.set(2, 70, False, time_text="1.5h")  # SDL даёт заряд грубо: ни процентов, ни часов на батарейке
    assert b.battery_text() == "средний"
    b.set(2, None, True)
    assert b.battery_text() == "заряжается"
    assert not b.grab().isNull()



def test_pad_badge_gear_bars_widen_and_flash(qtbot):
    b = PadBadge()
    qtbot.addWidget(b)
    b.set(1, 70, False)
    w0 = b.width()
    b.set(1, 70, False, gear=3, gears=8)
    assert b.gear_text() == "скорость 3 из 8"
    assert b.width() == w0 + PadBadge.gear_width(8)
    assert not b.flashing()  # первое появление ступени — без вспышки
    b.set(1, 70, False, gear=4, gears=8)
    assert b.flashing()  # L1/R1 сменили ступень — значок на миг лаймовый
    qtbot.waitUntil(lambda: not b.flashing(), timeout=2000)
    b.set(1, 70, False)  # режим не «предел скорости» — столбиков нет
    assert b.width() == w0 and b.gear_text() == ""
    assert not b.grab().isNull()


def test_bottom_plates_do_not_overlap_in_narrow_frame(qtbot):
    from PySide6.QtWidgets import QLabel, QWidget

    from ptztracker.ui.widgets import VideoFrame

    f = VideoFrame(QWidget())
    qtbot.addWidget(f)
    f.resize(420, 240)
    f.show()
    left, right = QLabel("Подсказка слева, довольно длинная строка"), QLabel("1280×720 · 25 · анализ 15")
    f.add_overlay(left, "bottom-left")
    f.add_overlay(right, "bottom-right")
    left.show()
    right.show()
    qtbot.wait(20)
    assert not left.geometry().intersects(right.geometry())
    f.resize(1200, 240)  # широко — снова рядом, на одной высоте
    qtbot.wait(20)
    assert left.geometry().bottom() == right.geometry().bottom()
