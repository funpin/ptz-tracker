"""Фоновый анализ видео: берёт свежий кадр, находит людей, ведёт номера и цель.

Работает в своём потоке, поэтому окно не тормозит даже при медленном распознавании:
кадры, пришедшие за время анализа, просто пропускаются.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from ptztracker.video.frames import FpsMeter, FrameSource
from ptztracker.vision.tracker import PersonTracker, Track
from ptztracker.vision.types import Detector

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisResult:
    seq: int
    t_capture: float
    frame_size: tuple[int, int]
    tracks: tuple[Track, ...]  # видимые в этом кадре + цель, даже если она сейчас не видна
    target_id: int | None
    target_state: str  # "none" | "tracking" | "lost"
    infer_ms: float
    fps: float
    detector: str
    device: str


class Analyzer:
    def __init__(
        self,
        source: FrameSource,
        detector_factory: Callable[[], Detector],
        max_fps: float = 15.0,
        tracker: PersonTracker | None = None,
    ):
        self.source = source
        self.detector_factory = detector_factory
        self.max_fps = max_fps
        self.tracker = tracker or PersonTracker()
        self.error: str | None = None
        self._lock = threading.Lock()  # трекер и последний результат
        self._latest: AnalysisResult | None = None
        self._fps = FpsMeter()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- управление ----------------------------------------------------

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="analyzer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def latest(self) -> AnalysisResult | None:
        with self._lock:
            return self._latest

    def select_at(self, x: float, y: float) -> int | None:
        with self._lock:
            tid = self.tracker.select_at(x, y)
            if tid is not None:
                self._refresh_target()
            return tid

    def clear_target(self) -> None:
        with self._lock:
            self.tracker.clear_target()
            self._refresh_target()

    def _refresh_target(self) -> None:
        """Сразу отразить смену цели в последнем результате (без ожидания следующего анализа)."""
        if self._latest is not None:
            self._latest = dataclasses.replace(
                self._latest, target_id=self.tracker.target_id, target_state=self.tracker.target_state
            )

    # --- поток анализа ------------------------------------------------

    def _run(self) -> None:
        try:
            detector = self.detector_factory()
        except Exception as e:
            log.error("Распознавание недоступно: %s", e)
            self.error = str(e)
            return
        period = 1.0 / self.max_fps
        last_seq = None
        while not self._stop.is_set():
            fr = self.source.latest()
            if fr is None or fr.seq == last_seq:
                self._stop.wait(0.005)
                continue
            last_seq = fr.seq
            t_start = time.monotonic()
            try:
                people = detector.detect(fr)
            except Exception as e:
                log.exception("Ошибка распознавания")
                self.error = str(e)
                self._stop.wait(0.5)
                continue
            infer_ms = (time.monotonic() - t_start) * 1000
            with self._lock:
                seen = self.tracker.update(people, fr.t_capture, fr.image)
                tracks = list(seen)
                tgt = self.tracker.target
                if tgt is not None and all(t.id != tgt.id for t in seen):
                    tracks.append(tgt)
                now = time.monotonic()
                self._fps.tick(now)
                self._latest = AnalysisResult(
                    seq=fr.seq,
                    t_capture=fr.t_capture,
                    frame_size=(fr.image.shape[1], fr.image.shape[0]),
                    tracks=tuple(dataclasses.replace(t, keypoints=t.keypoints.copy()) for t in tracks),
                    target_id=self.tracker.target_id,
                    target_state=self.tracker.target_state,
                    infer_ms=infer_ms,
                    fps=self._fps.fps(now),
                    detector=detector.name,
                    device=detector.device,
                )
                self.error = None
            wait = t_start + period - time.monotonic()
            if wait > 0:
                self._stop.wait(wait)
