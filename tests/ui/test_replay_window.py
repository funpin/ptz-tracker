"""Окно просмотра записи: кадр с рамками записанного анализа, ползунок, воспроизведение, коды регулятора."""

import cv2
import numpy as np
from PySide6.QtCore import Qt

from ptztracker.recorder.events import event_line
from ptztracker.recorder.reader import Episode
from ptztracker.recorder.writer import write_episode
from ptztracker.ui.replay_window import ReplayWindow

T0 = 200.0
N = 10


def _episode(tmp_path):
    """10 кадров 64×36 (кадр i залит яркостью 20·i), анализ в полном разрешении 1280×720, такты регулятора."""
    frames, events = [], []
    for i in range(N):
        t = T0 + i * 0.2
        ok, jpg = cv2.imencode(".jpg", np.full((36, 64, 3), 20 * i, np.uint8))
        frames.append((t, (i, 64, 36, jpg.tobytes())))
        tracks = [{"id": 3, "box": [100.0 + i, 100.0, 400.0, 700.0], "score": 0.9,
                   "keypoints": [[0.0, 0.0, 0.0]] * 17, "velocity": [0.0, 0.0], "hits": 5, "misses": 0,
                   "last_seen": t, "created": T0}]
        events.append((t + 0.02, event_line(t + 0.02, "analysis", {
            "seq": i, "t_capture": t, "frame_size": [1280, 720], "tracks": tracks,
            "target_id": 3, "target_state": "tracking" if i < 7 else "lost", "infer_ms": 15.0})))
        events.append((t + 0.05, event_line(t + 0.05, "control", {
            "t_capture": t, "codes": [50 + i, 49, 50], "out": {"state": "moving"}, "zoom_pos": 0x555})))
    return Episode.open(write_episode(tmp_path / "e.zip", {"version": "test"}, events, frames))


def test_replay_window_scrubs(qtbot, tmp_path):
    w = ReplayWindow(_episode(tmp_path))
    qtbot.addWidget(w)
    w.show()
    assert w.slider.maximum() == N - 1
    w.slider.setValue(5)
    assert w.index == 5
    fr = w.source.latest()
    assert abs(int(fr.image[18, 32, 0]) - 100) < 6  # показан кадр 5
    r = w.analysis.latest()
    assert r.target_id == 3 and r.target_state == "tracking" and r.frame_size == (1280, 720)
    assert r.tracks[0].box[0] == 105.0 and r.t_capture == T0 + 1.0
    qtbot.waitUntil(lambda: w.preview.has_frame(), timeout=2000)
    assert "55/49/50" in w.status.text() and "moving" in w.status.text()
    w.slider.setValue(8)
    assert w.analysis.latest().target_state == "lost"
    assert "58/49/50" in w.status.text()
    assert w.analysis.select_at(10, 10) is None  # только просмотр: цель не выбирается


def test_replay_window_plays(qtbot, tmp_path):
    w = ReplayWindow(_episode(tmp_path))
    qtbot.addWidget(w)
    w.show()
    w.speed_combo.setCurrentIndex(w.speed_combo.count() - 1)  # ×4
    assert w.speed == 4.0
    w.play_btn.click()
    assert w.playing and w.play_btn.text() == "⏸"
    qtbot.waitUntil(lambda: w.index == N - 1, timeout=3000)  # 1,8 с записи за ~0,45 с
    qtbot.waitUntil(lambda: not w.playing, timeout=1000)  # в конце — пауза
    assert w.play_btn.text() == "▶"
    w.play_btn.click()  # ▶ в конце — с начала
    assert w.playing
    qtbot.waitUntil(lambda: 0 < w.index < N - 1, timeout=2000)
    w.play_btn.click()
    assert not w.playing


def test_replay_window_caches_few_decoded_frames(qtbot, tmp_path):
    w = ReplayWindow(_episode(tmp_path))
    qtbot.addWidget(w)
    for i in range(N):
        w.slider.setValue(i)
    assert len(w.source._cache) <= w.source.CACHE_SIZE < N


def test_main_window_opens_replay(qtbot, tmp_path, monkeypatch):
    from ptztracker.config import ConfigStore
    from ptztracker.ui.main_window import MainWindow

    _episode(tmp_path)
    mw = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(mw)
    assert "Открыть запись…" in [a.text() for a in mw.service_menu.actions()]
    rw = mw.open_replay(tmp_path / "e.zip")
    assert rw is not None and rw.isVisible() and rw.slider.maximum() == N - 1
    # стрелки в окне записи листают ползунок, а не поворачивают камеру
    sim = mw.session.sim
    rw.slider.setFocus()
    qtbot.keyPress(rw.slider, Qt.Key_Right)
    qtbot.wait(300)
    qtbot.keyRelease(rw.slider, Qt.Key_Right)
    assert rw.index == 1
    assert all(c == "#PTS5050" for c in sim.received if c.startswith("#PTS"))
    rw.close()
    warned = []
    monkeypatch.setattr("ptztracker.ui.main_window.QMessageBox.warning", lambda *a: warned.append(a[2]))
    assert mw.open_replay(tmp_path / "нет.zip") is None
    assert "нет.zip" in warned[0]
    mw.close()
