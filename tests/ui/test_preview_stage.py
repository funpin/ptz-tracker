"""Область сцены на видео: показ (затемнение, линия) и настройка мышью. Макет — docs/design/mockups/stage-area.html."""

import time
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QPoint, Qt

from ptztracker.camera import he42
from ptztracker.control import stage_area as sa
from ptztracker.ui.preview import PreviewWidget
from ptztracker.video.frames import Frame, SourceStats
from ptztracker.vision.tracker import Track

VIEW = sa.CameraView(0.0, 0.0, he42.HFOV_WIDE_DEG)
AREA = sa.StageArea(-20.0, 20.0, -10.0, 10.0)
W, H = 1280, 720


class GreySource:
    def __init__(self):
        self.img = np.full((H, W, 3), 128, np.uint8)
        self.n = 0

    def latest(self):
        self.n += 1
        return Frame(self.img, time.monotonic(), self.n)

    def stats(self):
        return SourceStats(True, 25, W, H, None, "x")


def _track(i, cx, top, on_stage=True):
    kp = np.zeros((17, 3), np.float32)
    return Track(id=i, box=(cx - 40, top, cx + 40, top + 200), score=0.9, keypoints=kp, last_seen=1.0, on_stage=on_stage)


class FakeAnalyzer:
    def __init__(self, tracks, target=None):
        self.r = SimpleNamespace(seq=1, t_capture=1.0, frame_size=(W, H), tracks=tuple(tracks), target_id=target,
                                 target_state="tracking" if target is not None else "none")
        self.clicks = []

    def latest(self):
        return self.r

    def select_at(self, x, y):
        self.clicks.append((x, y))
        return None


def make(qtbot, area=AREA, view=VIEW, analyzer=None):
    p = PreviewWidget()
    qtbot.addWidget(p)
    p.resize(W, H)
    p.show()
    p.set_source(GreySource())
    p.set_analyzer(analyzer)
    p.stage_source = lambda t: (area, view)
    qtbot.waitUntil(p.has_frame, timeout=2000)
    return p


def at(p, x, y) -> QPoint:
    return p.frame_to_widget(x * W, y * H).toPoint()


def px(p, x, y) -> int:
    return p.grab().toImage().pixelColor(at(p, x, y)).red()


def frame_of(view, pan, tilt):
    return sa.to_frame(view, pan, tilt)


def test_area_dims_picture_outside_and_draws_line(qtbot):
    p = make(qtbot)
    inside, outside = px(p, 0.5, 0.5), px(p, 0.5, 0.95)
    assert inside == 128 and 70 < outside < 100  # вне области — затемнение 34 %
    edge = frame_of(VIEW, 0.0, -10.0)
    assert px(p, 0.5, edge[1]) > 150  # линия края светлее картинки


def test_no_area_off_area_or_unknown_view_draw_nothing(qtbot):
    for area, view in ((None, VIEW), (sa.StageArea(-20.0, 20.0, -10.0, 10.0, on=False), VIEW), (AREA, None)):
        p = make(qtbot, area, view)
        assert px(p, 0.5, 0.95) == 128 and px(p, 0.5, 0.5) == 128, (area, view)
        assert p.stage_outline() == []


def test_edge_outside_frame_has_no_line(qtbot):
    p = make(qtbot, sa.default_area(VIEW))
    assert px(p, 0.02, 0.3) == 128  # бок далеко за кадром: ни линии, ни затемнения
    assert px(p, 0.5, 0.9) < 100


def test_people_outside_area_get_no_brackets(qtbot):
    an = FakeAnalyzer([_track(1, 400, 200), _track(2, 800, 450, on_stage=False), _track(3, 1000, 450, on_stage=False)], target=3)
    p = make(qtbot, analyzer=an)
    assert [t.id for t in p.shown_tracks()] == [1, 3]  # цель рисуем и вне области — её ведут
    p.stage_source = lambda t: (None, VIEW)
    assert [t.id for t in p.shown_tracks()] == [1, 2, 3]


def drag(qtbot, p, a: QPoint, b: QPoint):
    qtbot.mousePress(p, Qt.LeftButton, pos=a)
    for k in (0.3, 0.7, 1.0):
        qtbot.mouseMove(p, a + (b - a) * k)
    qtbot.mouseRelease(p, Qt.LeftButton, pos=b)


def test_drag_side_in_edit_mode_changes_area_and_selects_nobody(qtbot):
    an = FakeAnalyzer([_track(1, 640, 200)])
    p = make(qtbot, analyzer=an)
    got, picked = [], []
    p.stageEdited.connect(lambda: got.append(p.stage_draft))
    p.targetClicked.connect(picked.append)
    p.begin_stage_edit(AREA)
    y1 = frame_of(VIEW, 0.0, -10.0)[1]
    drag(qtbot, p, at(p, 0.5, y1), at(p, 0.5, 0.8))
    assert got and p.stage_draft.tilt0 == sa.to_world(VIEW, 0.5, 0.8)[1].__class__(p.stage_draft.tilt0)
    assert abs(p.stage_draft.tilt0 - sa.to_world(VIEW, 0.5, 0.8)[1]) < 0.3
    assert (p.stage_draft.pan0, p.stage_draft.pan1, p.stage_draft.tilt1) == (-20.0, 20.0, 10.0)
    qtbot.mouseClick(p, Qt.LeftButton, pos=at(p, 0.5, 0.4))  # клик по человеку при настройке — не выбор
    assert an.clicks == [] and picked == []
    assert px(p, 0.5, 0.97) < 80  # при настройке затемнение сильнее (50 %)


def test_double_click_in_edit_mode_brings_hidden_sides_back(qtbot):
    p = make(qtbot, None)
    p.begin_stage_edit(sa.default_area(VIEW))
    qtbot.mouseDClick(p, Qt.LeftButton, pos=at(p, 0.5, 0.5))
    assert set(sa.handles(p.stage_draft, VIEW)) == {"pan0", "pan1", "tilt0", "tilt1"}


def test_end_of_edit_returns_normal_clicks(qtbot):
    an = FakeAnalyzer([_track(1, 640, 200)])
    p = make(qtbot, analyzer=an)
    p.begin_stage_edit(AREA)
    p.end_stage_edit()
    assert p.stage_draft is None
    qtbot.mouseClick(p, Qt.LeftButton, pos=at(p, 0.5, 0.4))
    assert len(an.clicks) == 1


def test_counts_people_in_area(qtbot):
    an = FakeAnalyzer([_track(1, 400, 300), _track(2, 800, 300), _track(3, 640, 600)])
    p = make(qtbot, analyzer=an)
    p.begin_stage_edit(AREA)
    assert p.stage_counts() == (2, 3)  # третий — ниже края (голова на y ≈ 620 из 720)
