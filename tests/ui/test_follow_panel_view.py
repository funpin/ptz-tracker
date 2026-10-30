from ptztracker.ui.follow_panel import FollowPanel
from ptztracker.ui.state import tracking_view


def test_set_view_updates_track_button_and_hint(qtbot):
    p = FollowPanel()
    qtbot.addWidget(p)
    v = tracking_view(True, None, 1, "lost")
    p.set_view(v)
    assert p.follow_btn.text() == "TRACK LOST"
    assert p.status_label.text() == v.why


def test_composition_buttons_show_digit(qtbot):
    p = FollowPanel()
    qtbot.addWidget(p)
    assert p.comp_buttons["full"].text().startswith("4 ")
    assert "клавиша 4" in p.comp_buttons["full"].toolTip()


def test_calibrate_signal(qtbot):
    p = FollowPanel()
    qtbot.addWidget(p)
    p.show()
    p.follow_btn.action_btn.show()
    p.set_view(tracking_view(False, None, None, None, diverged=True))
    with qtbot.waitSignal(p.calibrateRequested, timeout=500):
        p.follow_btn.action_btn.click()
