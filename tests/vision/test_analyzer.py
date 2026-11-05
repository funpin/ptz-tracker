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


def test_session_stop_stops_analyzer_quickly(ci_slack):
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"))
    s.start()
    time.sleep(1.0)
    t0 = time.monotonic()
    s.stop()
    assert time.monotonic() - t0 < 1.0 * ci_slack
    assert wait_until(lambda: not s.analyzer.is_running(), 1.0 * ci_slack)


def test_pose_detector_on_photo_through_analyzer():
    a = Analyzer(FakeSource(cv2.imread(str(PHOTO))), PoseDetector)
    a.start()
    try:
        assert wait_until(lambda: a.latest() is not None and len(a.latest().tracks) >= 3, 5)
        r = a.latest()
        assert r.detector == "Нейросеть" and r.device in ("CPU", "DirectML", "CoreML") and r.infer_ms > 0
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


def test_lock_not_held_while_computing_appearance(monkeypatch):
    # Final review I1: UI (latest()) не должен ждать, пока считаются гистограммы толпы
    import ptztracker.vision.analyzer as an
    from ptztracker.vision.types import Person

    real = an.compute_histograms

    def slow(image, people):
        time.sleep(0.3)
        return real(image, people)

    monkeypatch.setattr(an, "compute_histograms", slow)

    class One:
        name, device = "один", "CPU"

        def detect(self, frame):
            return [Person(box=(100, 100, 200, 400), score=0.9, keypoints=np.zeros((17, 3), np.float32))]

    a = Analyzer(FakeSource(np.zeros((720, 1280, 3), np.uint8)), One)
    a.start()
    try:
        assert wait_until(lambda: a.latest() is not None, 3)
        worst = 0.0
        for _ in range(40):
            t0 = time.monotonic()
            a.latest()
            worst = max(worst, time.monotonic() - t0)
            time.sleep(0.01)
        assert worst < 0.05
    finally:
        a.stop()


def test_detector_failure_logged_once_and_recreated(caplog):
    # Final review I3: не сыпать трейсбэками каждые 0.5 с; после серии сбоев пересоздать детектор
    import logging

    caplog.set_level(logging.WARNING)
    made = {"n": 0}

    class Flaky:
        name, device = "flaky", "DirectML"

        def __init__(self):
            made["n"] += 1
            self.gen = made["n"]

        def detect(self, frame):
            if self.gen == 1:
                raise RuntimeError("887A0005 device removed")
            return []

    monkeypatch_fail = Analyzer(FakeSource(np.zeros((360, 640, 3), np.uint8)), Flaky)
    monkeypatch_fail.RECREATE_AFTER = 3
    monkeypatch_fail.FAIL_PAUSE_S = 0.05
    monkeypatch_fail.start()
    try:
        assert wait_until(lambda: made["n"] >= 2 and monkeypatch_fail.latest() is not None, 3)
        assert monkeypatch_fail.error is None
        assert sum("Traceback" in (r.exc_text or "") or r.exc_info is not None for r in caplog.records) <= 1
    finally:
        monkeypatch_fail.stop()


def test_pose_detector_on_1280x720_frame_through_analyzer():
    """MJPEG (2): кадр 1280×720 — анализ работает, координаты в пикселях кадра, размер кадра в результате."""
    img = cv2.resize(cv2.imread(str(PHOTO)), (1280, 720))
    a = Analyzer(FakeSource(img), PoseDetector)
    a.start()
    try:
        assert wait_until(lambda: a.latest() is not None and len(a.latest().tracks) >= 3, 5)
        r = a.latest()
        assert r.frame_size == (1280, 720)
        # пиксели кадра, а не доли; рамка человека у края может чуть выйти за кадр
        assert max(t.box[2] for t in r.tracks) > 100
        assert all(-64 <= t.box[0] < t.box[2] <= 1280 + 64 and -36 <= t.box[1] < t.box[3] <= 720 + 36 for t in r.tracks)
    finally:
        a.stop()


class ListRecorder:
    active = True

    def __init__(self):
        self.events = []

    def frame(self, fr): ...

    def event(self, kind, **data):
        self.events.append((kind, data))

    def save_snapshot(self, reason): ...

    def close(self): ...


def test_analyzer_records_target_adoption_and_passes_camera_motion():
    """Цель перешла на новый трек на своём месте — событие target/adopt_place в чёрном ящике; сдвиг камеры доходит до трекера."""
    from ptztracker.vision.types import Person

    def box(cx, top):
        return Person(box=(cx - 20, top, cx + 20, top + 80), score=0.9, keypoints=np.zeros((17, 3), np.float32))

    calls = []
    phase = {"n": 0}  # 0 — двое стоят; потом цель пропала на 2 кадра; потом на её месте (сдвинутом) новый человек

    class ScriptDetector:
        name, device = "script", "cpu"

        def detect(self, frame):
            if phase["n"] == 0:
                return [box(100, 50), box(300, 50)]
            phase["n"] += 1
            if phase["n"] <= 3:
                return [box(100, 50)]
            return [box(100, 50), box(225, 50)]

    rec = ListRecorder()
    img = np.zeros((360, 640, 3), np.uint8)
    an = Analyzer(FakeSource(img), ScriptDetector, max_fps=100, recorder=rec)

    def motion(t0, t1):  # картинка уехала на 100 px влево, как только цель пропала
        calls.append((t0, t1))
        return 1.0, -100 / 640 if t1 - t0 > 0.05 else 0.0, 0.0

    an.motion = motion
    an.start()
    try:
        assert wait_until(lambda: an.latest() is not None and len(an.latest().tracks) >= 2, 3)
        tid = an.select_at(300, 90)
        assert tid is not None
        phase["n"] = 1
        assert wait_until(lambda: any(k == "target" and d.get("how", "").startswith("adopt") for k, d in rec.events), 3)
    finally:
        an.stop()
    ev = next(d for k, d in rec.events if k == "target" and d.get("how", "").startswith("adopt"))
    assert ev["how"] in ("adopt_place", "adopt_appearance") and ev["id"] == tid and ev["from_id"] != tid and calls
