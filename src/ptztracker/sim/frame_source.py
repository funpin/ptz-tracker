"""Видеопоток виртуальной камеры: «снимает» сцену окном по текущим pan/tilt/zoom с задержкой, как RTSP."""

from __future__ import annotations

import dataclasses
import threading
import time
from collections import deque

from ptztracker.sim.ptz_model import SimPtz
from ptztracker.sim.scene import view_matrix
from ptztracker.video.frames import FpsMeter, Frame, SourceStats


class SimFrameSource:
    def __init__(
        self,
        sim: SimPtz,
        scene,
        out_size: tuple[int, int] = (1280, 720),
        fps: float = 25.0,
        latency_s: float = 0.30,
        pan_span_deg: float = 120.0,
        tilt_span_deg: float = 40.0,
    ):
        self.sim = sim
        self.scene = scene
        self.out_size = out_size
        self.fps = fps
        self.latency_s = latency_s
        self.pan_span_deg = pan_span_deg
        self.tilt_span_deg = tilt_span_deg
        self._buf: deque[Frame] = deque(maxlen=int(fps * (latency_s + 1.0)) + 5)
        self._lock = threading.Lock()
        self._meter = FpsMeter()
        self._seq = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = time.monotonic()

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sim-video", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def latest(self) -> Frame | None:
        """Самый свежий кадр, «дошедший» до программы. Как у живой камеры, t_capture — момент получения
        (отрисовка + задержка), а не съёмки: регулятор сам вычитает задержку видео."""
        cutoff = time.monotonic() - self.latency_s
        with self._lock:
            for fr in reversed(self._buf):
                if fr.t_capture <= cutoff:
                    return dataclasses.replace(fr, t_capture=fr.t_capture + self.latency_s)
        return None

    def stats(self) -> SourceStats:
        fr = self.latest()
        return SourceStats(
            connected=fr is not None,
            fps=self._meter.fps(time.monotonic()),
            width=self.out_size[0],
            height=self.out_size[1],
            error=None if fr is not None else "Симулятор запускается…",
            url="sim://",
        )

    def render_now(self):
        return self._render()[0]

    def _render(self):
        """Кадр и служебные данные (истинная разметка, если сцена её умеет)."""
        w, h = self.scene.size
        with self.sim._lock:
            pan, tilt, hfov = self.sim.pan_deg, self.sim.tilt_deg, self.sim.hfov_deg()
        cx = w / 2 + pan / self.pan_span_deg * w
        cy = h / 2 - tilt / self.tilt_span_deg * h
        crop_w = w * hfov / self.pan_span_deg
        t = time.monotonic() - self._t0
        img = self.scene.render(t, cx, cy, crop_w, self.out_size)
        meta = None
        if hasattr(self.scene, "person_keypoints"):
            m = view_matrix(cx, cy, crop_w, self.out_size)
            meta = {"people": [self.scene.person_keypoints(t, m, self.out_size)]}
        return img, meta

    def _run(self) -> None:
        period = 1.0 / self.fps
        next_t = time.monotonic()
        while not self._stop.is_set():
            img, meta = self._render()
            now = time.monotonic()
            with self._lock:
                self._seq += 1
                self._buf.append(Frame(img, now, self._seq, meta))
                self._meter.tick(now)
            next_t += period
            delay = next_t - time.monotonic()
            if delay < 0:
                next_t = time.monotonic()
                delay = 0
            self._stop.wait(delay)
