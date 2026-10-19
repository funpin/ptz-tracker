import time

import numpy as np
from PySide6.QtCore import QPointF, Qt

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow
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


def test_widget_to_frame_letterbox(qtbot):
    p = PreviewWidget()
    qtbot.addWidget(p)
    p.resize(1000, 1000)
    p.show()
    p.set_source(StillSource())
    qtbot.waitUntil(p.has_frame, timeout=2000)
    x, y = p.widget_to_frame(QPointF(500, 500))
    assert abs(x - 640) < 2 and abs(y - 360) < 2
    assert p.widget_to_frame(QPointF(500, 10)) is None  # поле сверху
    back = p.frame_to_widget(640, 360)
    assert abs(back.x() - 500) < 2 and abs(back.y() - 500) < 2


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.resize(1400, 820)
    w.show()
    qtbot.waitUntil(lambda: w.session.analyzer.latest() is not None and len(w.session.analyzer.latest().tracks) > 0, timeout=4000)
    qtbot.waitUntil(w.preview.has_frame, timeout=2000)
    return w


def click_person(qtbot, w):
    tr = w.session.analyzer.latest().tracks[0]
    pt = w.preview.frame_to_widget((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)
    qtbot.mouseClick(w.preview, Qt.LeftButton, pos=pt.toPoint())
    return tr


def test_click_on_person_selects_target(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    with qtbot.waitSignal(w.preview.targetClicked, timeout=2000):
        click_person(qtbot, w)
    qtbot.waitUntil(lambda: "веду" in w.target_label.text(), timeout=3000)
    w.close()


def test_click_on_empty_keeps_target(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    click_person(qtbot, w)
    qtbot.waitUntil(lambda: "веду" in w.target_label.text(), timeout=3000)
    r = w.session.analyzer.latest()
    tr = next(t for t in r.tracks if t.id == r.target_id)
    empty = w.preview.frame_to_widget(5 if tr.box[0] > 50 else 1275, 5)
    qtbot.mouseClick(w.preview, Qt.LeftButton, pos=empty.toPoint())
    qtbot.wait(200)
    assert w.session.analyzer.latest().target_id == r.target_id
    w.close()


def test_reset_target_button(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    click_person(qtbot, w)
    qtbot.waitUntil(lambda: "веду" in w.target_label.text(), timeout=3000)
    w.reset_target_btn.click()
    qtbot.waitUntil(lambda: "не выбрана" in w.target_label.text(), timeout=2000)
    w.close()


def test_overlay_paints_without_errors(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    click_person(qtbot, w)
    w.skeleton_check.setChecked(True)
    assert w.preview.show_skeleton
    img = w.preview.grab()
    assert not img.isNull()
    w.close()


def test_status_shows_analysis(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: "анализ" in w.analysis_label.text() and "—" in w.analysis_label.text(), timeout=3000)
    w.close()
