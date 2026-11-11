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
                           effort=EffortProfile.at(0.5), x=SimpleNamespace(lead_now=0.0), t_late=0.31)
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
    p.zoneChanged.connect(lambda x, y, w, h: got.append((x, y, w, h)))
    with qtbot.waitSignal(p.zoneCommitted, timeout=1000):
        drag(qtbot, p, at(p, 0.6, 0.3), at(p, 0.5, 0.35))  # внутри мягкой зоны, мимо точки
    x, y, w, h = got[-1]
    assert abs(x - 0.40) < 0.01 and abs(y - 0.30) < 0.01 and (w, h) == (1.0, 1.0)


def test_drag_corner_scales_zone(qtbot):
    p = make(qtbot)
    p.zone_size_of = lambda: (1.0, 1.0)
    e = EffortProfile.at(0.5)
    got = []
    p.zoneChanged.connect(lambda x, y, w, h: got.append((x, y, w, h)))
    corner = at(p, 0.5 + e.soft_x, 0.25 + e.soft_y)  # правый нижний уголок мягкой зоны
    far = at(p, 0.5 + e.soft_x * 1.5, 0.25 + e.soft_y * 1.5)
    drag(qtbot, p, corner, far)
    x, y, w, h = got[-1]
    assert (x, y) == (0.5, 0.25)
    assert abs(w - 1.5) < 0.05 and abs(h - 1.5) < 0.05


def test_corner_drag_changes_width_and_height_apart(qtbot):
    """Вариант Б: за уголок скобки ширина и высота меняются отдельно — «широко по сцене, узко по высоте»."""
    p = make(qtbot)
    p.zone_size_of = lambda: (1.0, 1.0)
    e = EffortProfile.at(0.5)
    got = []
    p.zoneChanged.connect(lambda x, y, w, h: got.append((x, y, w, h)))
    drag(qtbot, p, at(p, 0.5 + e.soft_x, 0.25 + e.soft_y), at(p, 0.5 + e.soft_x * 1.6, 0.25 + e.soft_y * 0.7))
    x, y, w, h = got[-1]
    assert (x, y) == (0.5, 0.25)
    assert abs(w - 1.6) < 0.05 and abs(h - 0.7) < 0.05
    # левый верхний уголок — то же самое, от середины зоны
    got.clear()
    drag(qtbot, p, at(p, 0.5 - e.soft_x, 0.25 - e.soft_y), at(p, 0.5 - e.soft_x * 0.6, 0.25 - e.soft_y * 1.2))
    assert abs(got[-1][2] - 0.6) < 0.05 and abs(got[-1][3] - 1.2) < 0.05


def test_scale_is_relative_to_saved_zone(qtbot):
    p = make(qtbot)
    p.zone_size_of = lambda: (1.4, 0.8)
    e = EffortProfile.at(0.5)
    got = []
    p.zoneChanged.connect(lambda x, y, w, h: got.append((w, h)))
    drag(qtbot, p, at(p, 0.5 + e.soft_x, 0.25 + e.soft_y), at(p, 0.5 + e.soft_x * 2, 0.25 + e.soft_y * 2))
    assert abs(got[-1][0] - 1.8) < 0.01  # 1.4 × 2 — упёрлось в предел
    assert abs(got[-1][1] - 1.6) < 0.05  # 0.8 × 2


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


def test_zone_and_point_stay_put_while_walker_is_held_ahead(qtbot):
    f = follower()
    f.controller.x.lead_now = 0.1  # человек идёт: регулятор держит его впереди точки (этап 2)
    p = make(qtbot, f)
    soft, dead, pt = p._zone_rects()
    centre = at(p, 0.5, 0.25)
    assert abs(soft.center().x() - centre.x()) < 2 and abs(dead.center().x() - centre.x()) < 2  # рамка на месте
    assert abs(pt.x() - centre.x()) < 2  # и точка там, где её поставил оператор


def test_sim_cameras_keep_their_own_zones():
    """Запись F9 2026-11-10 13:40: зону потянули у «Симулятора 1» — она же оказалась у «Симулятора 2»."""
    from ptztracker.ui.main_window import SIM_CAMERA, sim_cameras

    cams = sim_cameras()
    cams[0].zones["full"] = [0.51, 0.17, 0.5]
    cams[0].unsupported.append("x")
    assert cams[1].zones == {} and cams[1].unsupported == []
    assert SIM_CAMERA.zones == {} and SIM_CAMERA.unsupported == []


def test_zone_text_tells_size_and_what_camera_can_hold(qtbot):
    """Плашка зоны (макет zone-boundary.html): размер в процентах кадра и что эта зона значит для этой камеры."""
    p = make(qtbot)
    assert p.zone_text() == "Зона ±22 % × ±18 % · хватает на спокойный шаг"
    f = follower()
    f.controller.t_late = 0.6  # медленный поток: та же зона уже плотнее, чем успевает камера
    assert make(qtbot, f).zone_text() == "Зона ±22 % × ±18 % · плотнее, чем успевает камера"
    f.enabled = False
    assert make(qtbot, f).zone_text() == ""


def test_zone_text_is_for_dragging_only(qtbot):
    p = make(qtbot)
    assert not p.zone_dragging()
    qtbot.mousePress(p, Qt.LeftButton, pos=at(p, 0.5 + 0.22, 0.25 + 0.18))  # за уголок — тянут сразу
    assert p.zone_dragging()
    qtbot.mouseRelease(p, Qt.LeftButton, pos=at(p, 0.5 + 0.22, 0.25 + 0.18))
    assert not p.zone_dragging()
