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
    assert c.combo.itemText(0) == "нет"
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
    c.set_pads(2)
    assert [b.isVisible() for b in c.pad_badges[:3]] == [True, True, False]
    c.set_pads(0)
    assert not any(b.isVisible() for b in c.pad_badges)


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
