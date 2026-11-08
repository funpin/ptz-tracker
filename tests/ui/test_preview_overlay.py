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
    qtbot.waitUntil(lambda: "Анализ" in w.analysis_pill.toolTip() and "к/с" in w.analysis_pill.text(), timeout=3000)
    w.close()


# --- запись видео: кнопка и плашка в углу ---


def rec_preview(qtbot):
    p = PreviewWidget()
    qtbot.addWidget(p)
    p.resize(800, 450)
    p.show()
    p.set_source(StillSource())
    qtbot.waitUntil(p.has_frame, timeout=2000)
    return p


def test_start_chip_appears_only_under_mouse(qtbot):
    from PySide6.QtCore import QPoint

    from ptztracker.videorec.recorder import IDLE

    p = rec_preview(qtbot)
    p.set_rec(IDLE)
    assert p.rec_rect() is None
    qtbot.mouseMove(p, QPoint(100, 100))
    qtbot.waitUntil(lambda: p.rec_rect() is not None, timeout=1000)
    assert p.rec_text() == "Запись"


def test_no_chip_without_camera(qtbot):
    from PySide6.QtCore import QPoint

    p = rec_preview(qtbot)
    p.set_rec(None)
    qtbot.mouseMove(p, QPoint(100, 100))
    qtbot.wait(50)
    assert p.rec_rect() is None


def test_click_on_chip_records_and_does_not_select(qtbot):
    from PySide6.QtCore import QPoint

    from ptztracker.videorec.recorder import RecState

    p = rec_preview(qtbot)
    p.set_rec(RecState(active=True, elapsed_s=754))
    clicks, selected = [], []
    p.rec_clicked.connect(lambda: clicks.append(1))
    p.targetClicked.connect(selected.append)
    guard = []
    p.click_guard = lambda: guard.append(1) or True
    c = p.rec_rect().center()
    qtbot.mouseClick(p, Qt.LeftButton, pos=QPoint(int(c.x()), int(c.y())))
    assert clicks == [1] and selected == [] and guard == []


def test_mouse_mode_does_not_catch_chip(qtbot):
    from PySide6.QtCore import QPoint

    from ptztracker.videorec.recorder import RecState

    p = rec_preview(qtbot)
    p.set_rec(RecState(active=True, elapsed_s=5))
    c = p.rec_rect().center()
    p.set_mouse_mode(True)
    clicks, left = [], []
    p.rec_clicked.connect(lambda: clicks.append(1))
    p.leftClicked.connect(lambda: left.append(1))
    qtbot.mouseClick(p, Qt.LeftButton, pos=QPoint(int(c.x()), int(c.y())))
    assert clicks == [] and left == [1]


def test_double_click_on_chip_does_not_enter_mouse_mode(qtbot):
    from PySide6.QtCore import QPoint

    from ptztracker.videorec.recorder import RecState

    p = rec_preview(qtbot)
    p.set_rec(RecState(active=True, elapsed_s=5))
    entered = []
    p.doubleClicked.connect(lambda: entered.append(1))
    c = p.rec_rect().center()
    qtbot.mouseDClick(p, Qt.LeftButton, pos=QPoint(int(c.x()), int(c.y())))
    assert entered == []


def test_rec_text_states(qtbot):
    from ptztracker.videorec.recorder import RecState

    p = rec_preview(qtbot)
    p.set_rec(RecState(active=True, elapsed_s=754))
    assert p.rec_text() == "REC 12:34"
    p.set_rec(RecState(active=True, signal=False, elapsed_s=754))
    assert p.rec_text() == "REC нет сигнала"


def test_stop_text_under_mouse(qtbot):
    from PySide6.QtCore import QPoint

    from ptztracker.videorec.recorder import RecState

    p = rec_preview(qtbot)
    p.set_rec(RecState(active=True, elapsed_s=754))
    c = p.rec_rect().center()
    qtbot.mouseMove(p, QPoint(int(c.x()), int(c.y())))
    qtbot.waitUntil(lambda: p.rec_text() == "■ Стоп", timeout=1000)


def test_plate_does_not_shrink_under_the_mouse(qtbot):
    from PySide6.QtCore import QPoint

    from ptztracker.videorec.recorder import RecState

    p = rec_preview(qtbot)
    p.set_rec(RecState(active=True, elapsed_s=754))
    r = p.rec_rect()
    left = QPoint(int(r.left()) + 3, int(r.center().y()))
    qtbot.mouseMove(p, left)
    qtbot.waitUntil(lambda: p.rec_text() == "■ Стоп", timeout=1000)
    assert p.rec_rect() == r
    clicks, selected = [], []
    p.rec_clicked.connect(lambda: clicks.append(1))
    p.targetClicked.connect(selected.append)
    qtbot.mouseClick(p, Qt.LeftButton, pos=left)
    assert clicks == [1] and selected == []


def test_degraded_recording_has_orange_dot(qtbot):
    from ptztracker.ui.theme import C
    from ptztracker.videorec.recorder import RecState

    p = rec_preview(qtbot)
    p.set_rec(RecState(active=True, elapsed_s=5))
    assert p.rec_dot_color() == C.REC
    p.set_rec(RecState(active=True, elapsed_s=5, degraded=True))
    assert p.rec_dot_color() == C.LOST
    assert p.rec_text() == "REC 00:05"

