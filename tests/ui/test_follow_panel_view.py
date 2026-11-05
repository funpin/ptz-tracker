from ptztracker.ui.follow_panel import FollowPanel
from ptztracker.ui.state import tracking_view


def test_set_view_updates_track_button_and_hint(qtbot):
    p = FollowPanel()
    qtbot.addWidget(p)
    v = tracking_view(True, None, 1, "lost")
    p.set_view(v)
    assert p.follow_btn.text() == "ИЩУ ЦЕЛЬ"
    assert p.status_label.text() == v.why


def test_three_silhouettes_with_keys(qtbot):
    p = FollowPanel()
    qtbot.addWidget(p)
    assert list(p.comp_buttons) == ["waist_center", "full", "wide"]
    assert [b.text() for b in p.comp_buttons.values()] == ["Пояс · 1", "Рост · 2", "Общий · 3"]
    assert "клавиша 2" in p.comp_buttons["full"].toolTip()


def test_slider_snaps_to_sizes(qtbot):
    p = FollowPanel()
    qtbot.addWidget(p)
    got = []
    p.compositionChanged.connect(got.append)
    p.size_slider.setValue(2)
    assert got == ["wide"] and p.comp_buttons["wide"].isChecked()
    p.comp_buttons["full"].click()
    assert got[-1] == "full" and p.size_slider.value() == 1
    p.set_composition("waist_left")  # задано агентом или старыми настройками — показываем «Пояс»
    assert p.comp_buttons["waist_center"].isChecked() and p.size_slider.value() == 0
    assert got[-1] == "full"  # показ не шлёт команду


def test_calibrate_signal(qtbot):
    p = FollowPanel()
    qtbot.addWidget(p)
    p.show()
    p.follow_btn.action_btn.show()
    p.set_view(tracking_view(False, None, None, None, diverged=True))
    with qtbot.waitSignal(p.calibrateRequested, timeout=500):
        p.follow_btn.action_btn.click()
