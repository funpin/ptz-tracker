from ptztracker.ui.follow_panel import FollowPanel
from ptztracker.ui.state import tracking_view
from ptztracker.ui.theme import H_STATE


def test_set_view_updates_tile_and_keeps_height(qtbot):
    p = FollowPanel()
    qtbot.addWidget(p)
    p.set_view(tracking_view(True, None, 1, "lost"))
    assert p.follow_btn.word_label.text() == "ЦЕЛЬ ПОТЕРЯНА"
    assert H_STATE == 118
    assert p.follow_btn.height() == H_STATE


def test_composition_buttons_show_digit(qtbot):
    p = FollowPanel()
    qtbot.addWidget(p)
    assert p.comp_buttons["full"].hotkey == "4"


def test_calibrate_signal(qtbot):
    p = FollowPanel()
    qtbot.addWidget(p)
    p.show()
    p.set_view(tracking_view(False, None, None, None, diverged=True))
    with qtbot.waitSignal(p.calibrateRequested, timeout=500):
        p.follow_btn.action_btn.click()
