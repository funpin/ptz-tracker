"""Повтор записанного эпизода через распознавание и трекер: цель «было/стало», смены и потери."""

import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from ptztracker.__main__ import main
from ptztracker.recorder.reader import Episode
from ptztracker.recorder.replay import replay_analysis
from ptztracker.recorder.writer import BlackBox
from ptztracker.video.frames import Frame
from ptztracker.vision.types import NUM_KP, Person

PHOTO = Path(__file__).resolve().parents[1] / "fixtures" / "people.jpg"
T0 = 500.0
DT = 0.2  # 5 к/с — как пишет чёрный ящик
# два человека на снимке, в долях кадра: левый и правый
BOXES = ((0.05, 0.2, 0.45, 0.95), (0.55, 0.2, 0.95, 0.95))
REC_IDS = (4, 7)  # номера в живом трекере (у повтора будут свои)


def _abs_box(frac, w, h):
    return (frac[0] * w, frac[1] * h, frac[2] * w, frac[3] * h)


class FixedDetector:
    """Детерминированный детектор: люди на одних и тех же местах кадра (в долях — работает при любом размере)."""

    name = "Тест"
    device = "—"

    def __init__(self, skip_right_at: set[int] = frozenset()):
        self.skip_right_at = skip_right_at  # номера вызовов, в которых правого «не видно»
        self.calls = 0

    def detect(self, frame: Frame) -> list[Person]:
        h, w = frame.image.shape[:2]
        n, self.calls = self.calls, self.calls + 1
        boxes = BOXES[:1] if n in self.skip_right_at else BOXES
        return [Person(box=_abs_box(b, w, h), score=0.9, keypoints=np.zeros((NUM_KP, 3), np.float32))
                for b in boxes]


def build_episode(tmp_path, n=20, click_at=0.5, click_frac=(0.75, 0.5), last_seen_jitter=0.0,
                  extra_events=()) -> Path:
    """Эпизод из people.jpg × n кадров: анализ в полном разрешении (810×1080), кадры в записи — 270×360."""
    img = cv2.imread(str(PHOTO))
    h, w = img.shape[:2]
    bb = BlackBox(tmp_path, {"version": "test", "stream_kind": "sim"}, frame_fps=5, start_thread=False)
    click_t = T0 + click_at
    bb.event("target", t=click_t, id=REC_IDS[1], how="click", x=click_frac[0] * w, y=click_frac[1] * h)
    for i in range(n):
        t = T0 + i * DT
        bb.frame(Frame(image=img, t_capture=t, seq=100 + i))
        selected = t > click_t
        tracks = [{"id": tid, "box": [round(v, 1) for v in _abs_box(b, w, h)], "score": 0.9,
                   "keypoints": [[0.0, 0.0, 0.0]] * NUM_KP, "velocity": [0.0, 0.0], "hits": i + 1, "misses": 0,
                   "last_seen": t + last_seen_jitter, "created": T0}
                  for tid, b in zip(REC_IDS, BOXES)]
        bb.event("analysis", t=t + 0.03, seq=100 + i, t_capture=t, frame_size=[w, h], tracks=tracks,
                 target_id=REC_IDS[1] if selected else None, target_state="tracking" if selected else "none",
                 infer_ms=20.0)
    for kind, data in extra_events:
        bb.event(kind, **data)
    bb.flush()
    path = bb.save_snapshot("повтор").result(timeout=10)
    bb.close()
    return path


def test_analysis_replay_on_fixture_episode(tmp_path):
    ep = Episode.open(build_episode(tmp_path))
    det = FixedDetector()
    r = replay_analysis(ep, detector=det)
    assert det.calls == 20 and len(r.t) == 20
    assert r.seq == list(range(100, 120))
    # щелчок в координатах полного кадра пересчитан в кадр записи: выбран правый человек
    assert r.click is not None and r.selected_id is not None
    assert abs(r.click[1] - 0.75 * 270) < 1 and abs(r.click[2] - 0.5 * 360) < 1
    # щелчок между кадрами 2 и 3 (t0+0.4 и t0+0.6): до него цели нет, после — держится до конца
    assert [tid for tid, _ in r.new[:3]] == [None] * 3
    assert all(tid == r.selected_id and st == "tracking" for tid, st in r.new[3:])
    box = r.new_box[10]
    assert box[0] > 0.5 * 270  # правая половина кадра
    s = r.summary()
    assert s.frames == 20
    assert s.switches_new == s.switches_rec == 0
    assert s.losses_new == s.losses_rec == 0
    assert s.agree_pct == pytest.approx(100.0)
    text = s.text()
    assert "Смены цели: было 0, стало 0" in text and "Совпадение рамки цели" in text


def test_recorded_target_box_tolerates_rounding(tmp_path):
    """last_seen и t_capture — одно живое значение, но в JSON могут разойтись в последних знаках."""
    ep = Episode.open(build_episode(tmp_path, last_seen_jitter=3e-7))
    r = replay_analysis(ep, detector=FixedDetector())
    assert all(b is not None for b in r.rec_box[3:])
    assert r.summary().agree_pct == pytest.approx(100.0)


def test_analysis_replay_skips_overflowing_seq(tmp_path):
    bad = ("analysis", {"t": T0 + 0.1, "seq": float("inf"), "t_capture": T0 + 0.1, "frame_size": [810, 1080],
                        "tracks": [], "target_id": None, "target_state": "none"})
    r = replay_analysis(Episode.open(build_episode(tmp_path, extra_events=[bad])), detector=FixedDetector())
    assert len(r.t) == 20 and r.skipped_events == 1
    assert "повреждённых строк записи" in r.summary().text()


def test_analysis_replay_counts_loss(tmp_path):
    ep = Episode.open(build_episode(tmp_path))
    r = replay_analysis(ep, detector=FixedDetector(skip_right_at={8}))
    assert r.new[8] == (r.selected_id, "lost")
    s = r.summary()
    assert s.losses_new == 1 and s.losses_rec == 0
    assert s.switches_new == 0  # вернулся тот же номер
    assert s.agree_pct < 100.0


def test_analysis_replay_without_click_has_no_target(tmp_path):
    ep = Episode.open(build_episode(tmp_path, click_at=100.0))  # щелчок после всех кадров
    r = replay_analysis(ep, detector=FixedDetector())
    assert all(tid is None for tid, _ in r.new)
    assert "не выбрана" in r.summary().text()


def test_analysis_replay_skips_detector_errors(tmp_path):
    class Flaky(FixedDetector):
        def detect(self, frame):
            if self.calls == 5:
                self.calls += 1
                raise RuntimeError("сбой")
            return super().detect(frame)

    r = replay_analysis(Episode.open(build_episode(tmp_path)), detector=Flaky())
    assert len(r.t) == 19 and r.failed_frames == 1
    assert "не распознано" in r.summary().text()


def test_cli_replay_analysis(tmp_path, capsys, monkeypatch):
    import ptztracker.vision.pose_detector as pd

    monkeypatch.setattr(pd, "PoseDetector", FixedDetector)
    path = build_episode(tmp_path)
    assert main(["--replay", str(path), "--mode", "analysis"]) == 0
    out = capsys.readouterr().out
    assert "Кадров: 20" in out and "Смены цели" in out


def test_frames_index_skips_damaged_lines(tmp_path):
    path = tmp_path / "e.zip"
    ok, jpg = cv2.imencode(".jpg", np.zeros((36, 64, 3), np.uint8))
    lines = [
        json.dumps({"t": 1.0, "seq": 1, "w": 64, "h": 36, "file": "frames/000001.jpg"}),
        "{обрыв записи",
        json.dumps({"t": 1.2, "seq": 2, "w": 64, "h": 36}),  # без файла
        json.dumps({"t": 1.4, "w": 64, "h": 36, "file": "frames/000003.jpg"}),  # без номера
        json.dumps({"t": 1.6, "seq": 4, "w": 64, "h": 36, "file": "frames/000004.jpg"}),  # файла нет в zip
        json.dumps({"t": 1.8, "seq": 5, "w": 64, "h": 36, "file": "frames/000005.jpg"}),
        '{"t": 2.0, "seq": 1e999, "w": 64, "h": 36, "file": "frames/000005.jpg"}',  # номер — бесконечность
    ]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("meta.json", "{}")
        z.writestr("events.jsonl", "мусор\n")
        z.writestr("frames.jsonl", "\n".join(lines) + "\n")
        for s in (1, 3, 5):
            z.writestr(f"frames/{s:06d}.jpg", jpg.tobytes())
    ep = Episode.open(path)
    assert ep.bad_lines == 1 + 4  # строка events + четыре негодные строки frames
    assert [s for _, s, _ in ep.frames()] == [1, 5]


@pytest.mark.slow
def test_analysis_replay_with_pose_detector(tmp_path):
    """Настоящая нейросеть на кадрах записи (270×360): цель, выбранная щелчком, держится."""
    from ptztracker.vision.pose_detector import PoseDetector

    det = PoseDetector()
    img = cv2.imread(str(PHOTO))
    small = cv2.resize(img, (270, 360), interpolation=cv2.INTER_AREA)
    people = det.detect(Frame(small, 0.0, 0))
    assert people
    b = max(people, key=lambda p: p.score).box
    k = img.shape[1] / small.shape[1]
    frac = ((b[0] + b[2]) / 2 * k / img.shape[1], (b[1] + b[3]) / 2 * k / img.shape[0])
    r = replay_analysis(Episode.open(build_episode(tmp_path, click_frac=frac)), detector=det)
    assert r.selected_id is not None
    s = r.summary()
    assert s.switches_new == 0 and s.losses_new == 0
    assert all(st == "tracking" for _, st in r.new[3:])
