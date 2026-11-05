from PySide6.QtCore import QPoint, Qt

from ptztracker.ui.camera_cell import CameraCell
from ptztracker.ui.widgets import StatusPill

ITEMS = [("cam-a", "PTZ 1"), ("cam-b", "PTZ 2")]


def make(qtbot, slot=0):
    c = CameraCell(slot)
    qtbot.addWidget(c)
    c.show()
    return c


def test_choices_contain_none_first(qtbot):
    c = make(qtbot)
    got = []
    c.cameraChosen.connect(lambda *a: got.append(a))
    c.set_choices(ITEMS, "cam-a")
    assert c.combo.itemText(0) == "нет камеры"
    assert c.combo.itemData(0) is None
    assert [c.combo.itemData(i) for i in range(1, 3)] == ["cam-a", "cam-b"]
    assert c.combo.currentData() == "cam-a"
    assert got == []


def test_choose_emits(qtbot):
    c = make(qtbot, 2)
    c.set_choices(ITEMS, None)
    got = []
    c.cameraChosen.connect(lambda s, i: got.append((s, i)))
    c.combo.setCurrentIndex(c.combo.findData("cam-b"))
    c.combo.setCurrentIndex(0)
    assert got == [(2, "cam-b"), (2, None)]


def test_click_unselected_only_selects(qtbot):
    c = make(qtbot)
    c.set_selected(False)
    got = []
    c.selectRequested.connect(got.append)
    qtbot.mouseClick(c.preview, Qt.LeftButton, pos=QPoint(5, 5))
    assert got == [0]
    c.set_selected(True)
    assert c.preview.click_guard() is True
    qtbot.mouseClick(c.preview, Qt.LeftButton, pos=QPoint(5, 5))
    qtbot.mouseClick(c.preview, Qt.RightButton, pos=QPoint(5, 5))
    assert got == [0]


def test_right_click_unselected_does_not_select(qtbot):
    c = make(qtbot)
    got = []
    c.selectRequested.connect(got.append)
    qtbot.mouseClick(c.preview, Qt.RightButton, pos=QPoint(5, 5))
    assert got == []


def test_set_choices_twice_and_unknown_current(qtbot):
    c = make(qtbot)
    c.set_choices(ITEMS, "cam-a")
    c.set_choices(ITEMS[:1], "zzz")
    assert c.combo.count() == 2
    assert c.combo.currentData() is None


def test_pads_badges(qtbot):
    c = make(qtbot)
    assert not c.pad_badges[0].isVisible()
    c.set_pads([(1, 83, False), (3, 18, False)])
    assert [b.isVisible() for b in c.pad_badges[:3]] == [True, True, False]
    assert c.pad_badges[0].battery_text() == "83" and c.more_pads.isHidden()
    c.set_pads([(1, 83, False), (2, 50, False), (3, None, False), (4, 10, True)])
    assert [b.isVisible() for b in c.pad_badges] == [True, False, False, False]
    assert c.more_pads.isVisible() and c.more_pads.text() == "+3"
    assert "3" in c.more_pads.toolTip() and "10%" in c.more_pads.toolTip()
    c.set_pads([])
    assert not any(b.isVisible() for b in c.pad_badges) and c.more_pads.isHidden()


def test_header_tags_and_power(qtbot):
    c = make(qtbot)
    c.set_choices(ITEMS, "cam-a")
    c.set_header(tally="pgm", state="on", sim=True, power="on")
    assert c.tally_tag.text() == "PGM" and c.state_tag.text() == "ВЕДУ" and c.sim_tag.isVisible()
    assert c.power_btn.isVisible() and c.more_btn.isVisible()
    c.set_header(tally=None, state="standby", sim=False, power="standby")
    assert c.tally_tag.isHidden() and c.state_tag.text() == "ОЖИДАНИЕ" and c.sim_tag.isHidden()
    got = []
    c.powerClicked.connect(got.append)
    c.power_btn.click()
    assert got == [0]
    c.set_choices(ITEMS, None)
    c.set_header(tally=None, state=None, sim=False, power=None)
    assert c.power_btn.isHidden() and c.more_btn.isHidden() and c.state_tag.isHidden()


def test_header_never_overflows(qtbot):
    c = make(qtbot)
    c.set_choices([("x", "Очень длинное имя камеры на сцене слева у окна")], "x")
    c.set_header(tally="pgm", state="no_analysis", sim=True, power="on")
    c.set_pads([(1, 83, False), (2, 50, False), (3, 40, False)])
    c.resize(560, 300)
    qtbot.wait(50)
    for w in (c.more_btn, c.power_btn, c.more_pads, c.pad_badges[0]):
        x = w.mapTo(c, QPoint(0, 0)).x()
        assert x >= 0 and x + w.width() <= c.width(), w


def test_corner_info(qtbot):
    c = make(qtbot)
    c.set_info("640×360 · 25 · анализ 15")
    assert c.info_label.text() == "640×360 · 25 · анализ 15" and not c.info_label.isHidden()
    c.set_info("")
    assert c.info_label.isHidden()


def test_service_shown_without_camera(qtbot):
    c = make(qtbot)
    assert c.video_frame.isVisible()
    svc = StatusPill("gamepad")
    c.set_service(svc)
    assert svc.isVisible() and not c.video_frame.isVisible()
    c.set_service(None)
    assert c.video_frame.isVisible() and not svc.isVisible()
    assert c.number_label.text().startswith("1")


def test_service_replaced_and_none_without_service(qtbot):
    c = make(qtbot)
    c.set_service(None)
    assert c.video_frame.isVisible()
    a, b = StatusPill("gamepad"), StatusPill("gamepad")
    c.set_service(a)
    c.set_service(b)
    assert b.isVisible() and a.parent() is None
