import threading
import time
from pathlib import Path

import cv2
import numpy as np

from ptztracker.camera.session import CameraSession
from ptztracker.config import CameraConfig
from ptztracker.video.frames import Frame, SourceStats
from ptztracker.vision.analyzer import Analyzer
from ptztracker.vision.pose_detector import PoseDetector

PHOTO = Path(__file__).resolve().parents[1] / "fixtures" / "people.jpg"


def wait_until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class FakeSource:
    """Отдаёт новый кадр каждые 40 мс (25 к/с)."""

    def __init__(self, image):
        self.image = image
        self.t0 = time.monotonic()

    def latest(self):
        seq = int((time.monotonic() - self.t0) / 0.04) + 1
        return Frame(self.image, time.monotonic(), seq)

    def stats(self):
        return SourceStats(True, 25.0, self.image.shape[1], self.image.shape[0], None, "fake")

    def start(self): ...

    def stop(self, timeout=2.0): ...


def test_analyzer_on_sim_tracks_and_selects():
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"))
    s.start()
    try:
        assert wait_until(lambda: s.analyzer.latest() and s.analyzer.latest().tracks, 3)
        r = s.analyzer.latest()
        tr = r.tracks[0]
        cx, cy = (tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2
        assert s.analyzer.select_at(cx, cy) == tr.id
        assert s.analyzer.latest().target_id == tr.id  # сразу видно в UI, не ждём следующий анализ
        assert wait_until(lambda: s.analyzer.latest().target_state == "tracking", 2)
        assert r.detector == "Симулятор" and 0 < r.fps <= 16
        s.analyzer.clear_target()
        assert s.analyzer.latest().target_id is None
    finally:
        s.stop()


def test_detector_failure_reported_not_fatal():
    def bad():
        raise RuntimeError("модель не найдена")

    a = Analyzer(FakeSource(np.zeros((360, 640, 3), np.uint8)), bad)
    a.start()
    try:
        assert wait_until(lambda: a.error, 2)
        assert "модель" in a.error and a.latest() is None
    finally:
        a.stop()


def test_slow_detector_does_not_block_and_drops_frames():
    class Slow:
        name, device = "медленный", "CPU"

        def detect(self, frame):
            time.sleep(0.2)
            return []

    a = Analyzer(FakeSource(np.zeros((360, 640, 3), np.uint8)), Slow)
    a.start()
    try:
        assert wait_until(lambda: a.latest() is not None, 2)
        s1 = a.latest().seq
        time.sleep(1.0)
        r = a.latest()
        assert r.seq - s1 >= 8  # пропускает кадры, берёт свежие
        assert 3 <= r.fps <= 6
    finally:
        t0 = time.monotonic()
        a.stop()
        assert time.monotonic() - t0 < 1.0


def test_session_stop_stops_analyzer_quickly():
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"))
    s.start()
    time.sleep(1.0)
    t0 = time.monotonic()
    s.stop()
    assert time.monotonic() - t0 < 1.0
    assert wait_until(lambda: not s.analyzer.is_running(), 1.0)


def test_pose_detector_on_photo_through_analyzer():
    a = Analyzer(FakeSource(cv2.imread(str(PHOTO))), PoseDetector)
    a.start()
    try:
        assert wait_until(lambda: a.latest() is not None and len(a.latest().tracks) >= 3, 5)
        r = a.latest()
        assert r.detector == "Нейросеть" and r.device in ("CPU", "DirectML") and r.infer_ms > 0
    finally:
        a.stop()


def test_analysis_can_be_disabled():
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"), analysis=False)
    s.start()
    try:
        assert s.analyzer is None
    finally:
        s.stop()


def test_target_lost_while_others_visible_keeps_running():
    from ptztracker.vision.types import Person

    def person(cx):
        kp = np.zeros((17, 3), np.float32)
        return Person(box=(cx - 50, 100, cx + 50, 400), score=0.9, keypoints=kp)

    phase = {"n": 0}

    class Scripted:
        name, device = "скрипт", "CPU"

        def detect(self, frame):
            phase["n"] += 1
            return [person(200), person(900)] if phase["n"] < 5 else [person(900)]

    a = Analyzer(FakeSource(np.zeros((720, 1280, 3), np.uint8)), Scripted)
    a.start()
    try:
        assert wait_until(lambda: a.latest() is not None and len(a.latest().tracks) == 2, 2)
        assert a.select_at(200, 250) is not None
        assert wait_until(lambda: phase["n"] > 8, 3)
        r = a.latest()
        assert a.is_running() and r.target_state == "lost"
        assert any(t.id == r.target_id for t in r.tracks)  # последняя известная рамка цели доступна UI
    finally:
        a.stop()
