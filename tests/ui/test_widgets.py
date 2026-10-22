import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from ptztracker.ui.theme import H_STATE, SP, apply_dark_theme
from ptztracker.ui.widgets import Chip, KeyButton, SectionHeader, StateTile, VideoFrame

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


def test_state_tile_height_constant(qtbot):
    t = StateTile()
    qtbot.addWidget(t)
    t.resize(360, 118)
    t.show()
    for k in KINDS:
        t.set_view(k, "СЛОВО", "коротко", k == "swing")
        assert t.height() == H_STATE == 118
        t.set_view(k, "СЛОВО", "очень длинная причина " * 10, k == "swing")
        assert t.height() == 118
    long = "x " * 100
    t.set_view("off", "ВЫКЛ", long, False)
    assert long in t.why_label.toolTip()
    assert t.why_label.text().count("\n") <= 1
    assert t.why_label.text().endswith("…")
    assert t.toolTip() == long
    assert t.layout().minimumSize().height() <= H_STATE


def test_state_tile_swing_reason_fits(qtbot):
    t = StateTile()
    qtbot.addWidget(t)
    t.resize(348, H_STATE)
    t.show()
    t.set_view("swing", "РАСКАЧКА", "Слежение остановлено: камера быстрее, чем ожидалось.", True)
    qtbot.wait(50)
    assert not t.why_label.text().endswith("…")
    # пояснение на всю ширину плитки: на Windows шрифт шире, и кнопка рядом съедала текст
    assert t.action_btn.isVisible()
    assert t.why_label.width() >= t.width() - 2 * SP[2] - 2


def test_state_tile_action_only_for_swing(qtbot):
    t = StateTile()
    qtbot.addWidget(t)
    for k in KINDS:
        t.set_view(k, "w", "p", k == "swing")
        assert t.action_btn.isHidden() == (k != "swing")


def test_state_tile_action_signal(qtbot):
    t = StateTile()
    qtbot.addWidget(t)
    t.show()
    t.set_view("swing", "w", "p", True)
    with qtbot.waitSignal(t.actionClicked, timeout=500):
        t.action_btn.click()


def test_state_tile_click_toggles(qtbot):
    t = StateTile()
    qtbot.addWidget(t)
    t.click()
    assert t.isChecked()


def test_state_tile_blinks_only_when_lost(qtbot):
    t = StateTile()
    qtbot.addWidget(t)
    t.set_view("lost", "w", "p", False)
    assert t._blink.isActive()
    t.set_view("on", "w", "p", False)
    assert not t._blink.isActive()


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
