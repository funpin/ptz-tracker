import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from ptztracker.ui.theme import apply_dark_theme
from ptztracker.ui.widgets import Chip, KeyButton, SectionHeader, TrackButton, VideoFrame

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
    expected = {"off": "TRACK OFF", "on": "TRACK ON", "lost": "TRACK LOST", "remote": "TRACK OFF",
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
