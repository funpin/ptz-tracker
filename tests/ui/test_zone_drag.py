import time
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QPoint, Qt

from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.ui.preview import PreviewWidget
from ptztracker.video.frames import Frame, SourceStats


class StillSource:
    def __init__(self):
        self.img = np.zeros((720, 1280, 3), np.uint8)
        self.n = 0

    def latest(self):
        self.n += 1
        return Frame(self.img, time.monotonic(), self.n)

    def stats(self):
        return SourceStats(True, 25, 1280, 720, None, "x")


def follower(anchor=(0.5, 0.25)):
    import dataclasses

    ctrl = SimpleNamespace(composition=dataclasses.replace(COMPOSITIONS["waist_center"], anchor=anchor),
                           effort=EffortProfile.at(0.5), x=SimpleNamespace(lead=0.0))
    return SimpleNamespace(enabled=True, output=object(), controller=ctrl)


def make(qtbot, f=None):
    p = PreviewWidget()
    qtbot.addWidget(p)
    p.resize(1280, 720)
    p.show()
    p.set_source(StillSource())
    p.set_follow(f or follower())
    qtbot.waitUntil(p.has_frame, timeout=2000)
    return p


def at(p, x, y) -> QPoint:
    return p.frame_to_widget(x * 1280, y * 720).toPoint()


def drag(qtbot, p, a: QPoint, b: QPoint):
    qtbot.mousePress(p, Qt.LeftButton, pos=a)
    for k in (0.3, 0.7, 1.0):
        qtbot.mouseMove(p, a + (b - a) * k)
    qtbot.mouseRelease(p, Qt.LeftButton, pos=b)


def test_drag_inside_zone_moves_face_point(qtbot):
    p = make(qtbot)
    got = []
    p.zoneChanged.connect(lambda x, y, s: got.append((x, y, s)))
    with qtbot.waitSignal(p.zoneCommitted, timeout=1000):
        drag(qtbot, p, at(p, 0.6, 0.3), at(p, 0.5, 0.35))  # внутри мягкой зоны, мимо точки
    x, y, s = got[-1]
    assert abs(x - 0.40) < 0.01 and abs(y - 0.30) < 0.01 and s == 1.0


def test_drag_corner_scales_zone(qtbot):
    p = make(qtbot)
    p.zone_scale_of = lambda: 1.0
    e = EffortProfile.at(0.5)
    got = []
    p.zoneChanged.connect(lambda x, y, s: got.append((x, y, s)))
    corner = at(p, 0.5 + e.soft_x, 0.25 + e.soft_y)  # правый нижний уголок мягкой зоны
    far = at(p, 0.5 + e.soft_x * 1.5, 0.25 + e.soft_y * 1.5)
    drag(qtbot, p, corner, far)
    x, y, s = got[-1]
    assert (x, y) == (0.5, 0.25)
    assert abs(s - 1.5) < 0.05


def test_scale_is_relative_to_saved_zone(qtbot):
    p = make(qtbot)
    p.zone_scale_of = lambda: 1.4
    e = EffortProfile.at(0.5)
    got = []
    p.zoneChanged.connect(lambda x, y, s: got.append(s))
    drag(qtbot, p, at(p, 0.5 + e.soft_x, 0.25 + e.soft_y), at(p, 0.5 + e.soft_x * 2, 0.25 + e.soft_y * 2))
    assert abs(got[-1] - 1.8) < 0.01  # 1.4 × 2 — упёрлось в предел


def test_click_without_drag_does_not_touch_zone(qtbot):
    p = make(qtbot)
    got = []
    p.zoneChanged.connect(lambda *a: got.append(a))
    p.zoneCommitted.connect(lambda: got.append("commit"))
    qtbot.mouseClick(p, Qt.LeftButton, pos=at(p, 0.6, 0.3))
    assert got == []


def test_double_click_in_zone_resets(qtbot):
    p = make(qtbot)
    with qtbot.waitSignal(p.zoneReset, timeout=1000):
        qtbot.mouseDClick(p, Qt.LeftButton, pos=at(p, 0.6, 0.3))


def test_no_zone_when_not_following(qtbot):
    f = follower()
    f.enabled = False
    p = make(qtbot, f)
    got = []
    p.zoneChanged.connect(lambda *a: got.append(a))
    drag(qtbot, p, at(p, 0.6, 0.3), at(p, 0.4, 0.3))
    assert got == []


def test_guard_blocks_zone_drag_in_unselected_cell(qtbot):
    p = make(qtbot)
    p.click_guard = lambda: False
    got = []
    p.zoneChanged.connect(lambda *a: got.append(a))
    drag(qtbot, p, at(p, 0.6, 0.3), at(p, 0.4, 0.3))
    assert got == []


def test_zone_stays_put_while_point_leads_the_walker(qtbot):
    f = follower()
    f.controller.x.lead = -0.1  # человек идёт: регулятор оставляет место для взгляда
    p = make(qtbot, f)
    soft, dead, pt = p._zone_rects()
    centre = at(p, 0.5, 0.25)
    assert abs(soft.center().x() - centre.x()) < 2 and abs(dead.center().x() - centre.x()) < 2  # рамка на месте
    assert abs(pt.x() - at(p, 0.4, 0.25).x()) < 2  # сдвиг видно только по точке
