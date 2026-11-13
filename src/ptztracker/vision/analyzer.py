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

from ptztracker.recorder import NullRecorder, Recorder, safe_event, safe_frame
from ptztracker.recorder.events import track_to_json
from ptztracker.video.frames import FpsMeter, FrameSource
from ptztracker.vision.tracker import PersonTracker, Track, compute_histograms
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
    RECREATE_AFTER = 5  # столько сбоев подряд — пересоздать детектор (видеокарта «отвалилась»)
    FAIL_PAUSE_S = 0.5

    def __init__(
        self,
        source: FrameSource,
        detector_factory: Callable[[], Detector],
        max_fps: float = 15.0,
        tracker: PersonTracker | None = None,
        recorder: Recorder | None = None,
    ):
        self.source = source
        self.detector_factory = detector_factory
        self.max_fps = max_fps
        self.tracker = tracker or PersonTracker()
        self.recorder: Recorder = recorder or NullRecorder()
        self.error: str | None = None
        # как камера сама сдвинула картинку между двумя кадрами (Follower.picture_motion); None — камера стоит
        self.motion: Callable[[float, float], tuple[float, float, float]] | None = None
        # область сцены: (люди, момент кадра, размер кадра) → кто из них на сцене; None — области нет (ставит сессия)
        self.stage: Callable[[list, float, tuple[int, int]], list[bool] | None] | None = None
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
        # x, y — чтобы повтор распознавания выбрал цель тем же щелчком
        safe_event(self.recorder, "target", id=tid, how="click", x=round(float(x), 1), y=round(float(y), 1))
        return tid

    def select_neighbor(self, direction: int) -> int | None:
        """Цель на соседа слева (−1) / справа (+1) по центру рамки; нет цели — от центра кадра."""
        with self._lock:
            r = self._latest
            if r is None:
                return None
            tgt = next((t for t in r.tracks if t.id == self.tracker.target_id), None)
            ref = (tgt.box[0] + tgt.box[2]) / 2 if tgt is not None else r.frame_size[0] / 2
            best, best_d = None, None
            for t in self._own_choice([t for t in r.tracks if t.last_seen == r.t_capture
                                       and (tgt is None or t.id != tgt.id)]):
                dx = ((t.box[0] + t.box[2]) / 2 - ref) * (1 if direction > 0 else -1)
                if dx > 0 and (best_d is None or dx < best_d):
                    best, best_d = t, dx
            if best is None or not self.tracker.select(best.id):
                return None
            self._refresh_target()
            tid = best.id
        # x, y — центр рамки: повтор распознавания выбирает цель так же, как по щелчку
        cx, cy = (best.box[0] + best.box[2]) / 2, (best.box[1] + best.box[3]) / 2
        safe_event(self.recorder, "target", id=tid, how="pad_right" if direction > 0 else "pad_left",
                   x=round(float(cx), 1), y=round(float(cy), 1))
        return tid

    @staticmethod
    def _own_choice(tracks: list) -> list:
        """Из кого программа выбирает сама (сосед, «ближе всех к центру»): люди в области сцены; там никого — все."""
        return [t for t in tracks if t.on_stage] or tracks

    def central_track(self):
        """Человек ближе всех к центру кадра — для выбора одной кнопкой; None — в кадре никого."""
        with self._lock:
            r = self._latest
            if r is None or not r.tracks:
                return None
            cx, cy = r.frame_size[0] / 2, r.frame_size[1] / 2
            return min(self._own_choice(list(r.tracks)),
                       key=lambda t: ((t.box[0] + t.box[2]) / 2 - cx) ** 2 + ((t.box[1] + t.box[3]) / 2 - cy) ** 2)

    def clear_target(self) -> None:
        with self._lock:
            self.tracker.clear_target()
            self._refresh_target()
        safe_event(self.recorder, "target", id=None, how="reset")

    def _refresh_target(self) -> None:
        """Сразу отразить смену цели в последнем результате (без ожидания следующего анализа)."""
        if self._latest is not None:
            self._latest = dataclasses.replace(
                self._latest, target_id=self.tracker.target_id, target_state=self.tracker.target_state
            )

    # --- поток анализа ------------------------------------------------

    def _record(self, fr, r: AnalysisResult) -> None:
        """Кадр и результат анализа — в запись (вне блокировки: окно не ждёт)."""
        rec = self.recorder
        if not rec.active:
            return
        safe_frame(rec, fr)
        try:
            tracks = [track_to_json(t) for t in r.tracks]
        except Exception as e:
            tracks = None
            safe_event(rec, "note", error=f"цели не переведены в JSON: {e}")
        safe_event(
            rec, "analysis", seq=r.seq, t_capture=r.t_capture, frame_size=list(r.frame_size), tracks=tracks,
            target_id=r.target_id, target_state=r.target_state, infer_ms=round(r.infer_ms, 2),
        )

    def _on_stage(self, people, fr) -> list[bool] | None:
        if self.stage is None:
            return None
        try:
            out = self.stage(people, fr.t_capture, (fr.image.shape[1], fr.image.shape[0]))
        except Exception as e:  # область не должна ронять анализ
            log.debug("Область сцены не посчитана: %s", e)
            return None
        return out if out is not None and len(out) == len(people) else None

    def _run(self) -> None:
        try:
            detector = self.detector_factory()
        except Exception as e:
            log.error("Распознавание недоступно: %s", e)
            self.error = str(e)
            return
        period = 1.0 / self.max_fps
        last_seq = None
        failures = 0
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
                failures += 1
                if failures == 1:
                    log.exception("Ошибка распознавания")
                else:
                    log.warning("Ошибка распознавания (%d подряд): %s", failures, e)
                self.error = str(e)
                if failures % self.RECREATE_AFTER == 0:
                    try:
                        detector = self.detector_factory()
                        log.info("Детектор пересоздан (%s)", detector.device)
                    except Exception as e2:
                        log.warning("Не удалось пересоздать детектор: %s", e2)
                self._stop.wait(self.FAIL_PAUSE_S)
                continue
            failures = 0
            infer_ms = (time.monotonic() - t_start) * 1000
            hists = compute_histograms(fr.image, people)  # дорого — вне блокировки, чтобы окно не ждало
            on_stage = self._on_stage(people, fr)
            with self._lock:
                seen = self.tracker.update(people, fr.t_capture, fr.image, hists, motion=self.motion,
                                           on_stage=on_stage)
                adopted = self.tracker.pop_adoptions()
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
                result = self._latest
            for a in adopted:  # цель перешла на другой трек — в чёрный ящик
                safe_event(self.recorder, "target", how="adopt_" + a["how"], id=a["id"], from_id=a["from_id"],
                           box=a["box"], lost_s=a["lost_s"], t_capture=a["t"], stage=a.get("stage", True))
            self._record(fr, result)
            wait = t_start + period - time.monotonic()
            if wait > 0:
                self._stop.wait(wait)
