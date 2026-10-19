"""Мастер калибровки: измеряет реальные скорости камеры по картинке (~1–2 минуты).

Камера ненадолго поворачивается в обе стороны на разных кодах скорости и зумах, затем
возвращается на место. По сдвигу кадров (фазовая корреляция) получается таблица
«код → доли кадра в секунду», время реакции и разгона, скорость зума.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from datetime import datetime
from typing import Callable

from ptztracker.camera import protocol as P
from ptztracker.control.camera_model import Calibration, SpeedTable, ZoomTable, default_calibration
from ptztracker.diagnostics.motion import estimate_scale, measure_shift, wait_for_motion
from ptztracker.diagnostics.probe import Pacer, probe_run
from ptztracker.video.frames import FrameSource

log = logging.getLogger(__name__)

DEFAULT_CODES = (53, 55, 57, 59, 61, 63, 65, 67, 70, 75, 80, 90)
ZOOM_SETTLE_S = 2.0
FLAT_FW_S = 0.004  # быстрые коды дали меньше — картинка не даёт измерить сдвиг
STEP_CODE = 70
ZOOM_CODES = (60, 75, 90)
ZOOM_RUN_S = 0.6
PROBE_MAX_S, PROBE_MAX_FRACTION = 2.5, 0.9


def default_zooms(start_zoom: int) -> list[int]:
    """Широкий угол, текущий зум (рабочий) и тело — регулятор не должен экстраполировать."""
    return sorted({P.LENS_MIN, start_zoom, (start_zoom + P.LENS_MAX) // 2, P.LENS_MAX})


def drop_flat_curves(curves: dict[int, list[tuple[int, float]]]) -> dict[int, list[tuple[int, float]]]:
    """Отбросить зумы, где картинка не дала измерить сдвиг (быстрые коды «стоят»)."""
    good = {}
    for z, curve in curves.items():
        fast = [v for c, v in curve if c >= 65]
        if fast and sum(1 for v in fast if v < FLAT_FW_S) <= len(fast) / 2:
            good[z] = curve
        else:
            log.warning("Зум %03X: картинка не дала измерить скорость — замеры отброшены", z)
    return good


class _Sender:
    """Pacer поверх произвольной функции отправки (клиент сессии или отдельный)."""

    def __init__(self, send: Callable[[str], str]):
        self.send = send


class CalibrationRunner:
    def __init__(
        self,
        client_send: Callable[[str], str],
        source: FrameSource,
        progress: Callable[[str], None],
        zooms: list[int] | None = None,
        codes: tuple[int, ...] = DEFAULT_CODES,
        stream_kind: str = "mjpeg",
    ):
        self.pacer = Pacer(_Sender(client_send))
        self.source = source
        self.progress = progress
        self.zooms = zooms
        self.codes = codes
        self.stream_kind = stream_kind

    # --- шаги -----------------------------------------------------------

    def _frame_width(self) -> int:
        fr = self.source.latest()
        if fr is None:
            raise RuntimeError("Нет видео с камеры — калибровка невозможна")
        return fr.image.shape[1]

    def _pan_curve(self, zoom: int) -> list[tuple[int, float]]:
        self.pacer.send(P.zoom_abs(zoom))
        time.sleep(ZOOM_SETTLE_S)
        width = self._frame_width()
        curve = []
        for i, code in enumerate(self.codes):
            c = code if i % 2 == 0 else 100 - code  # попеременно вправо/влево — камера остаётся на месте
            self.progress(f"Зум {zoom:03X}: скорость {code}…")
            # дольше и дальше, чем в диагностике: камера разгоняется ~0,85 с, нужна установившаяся скорость
            run = probe_run(self.pacer, self.source, c, max_s=PROBE_MAX_S, max_fraction=PROBE_MAX_FRACTION)
            curve.append((code, abs(run["px_per_s"]) / width))
            time.sleep(0.4)
        # скорость не может падать с ростом кода — сгладить выбросы
        out, best = [], 0.0
        for code, v in sorted(curve):
            best = max(best, v)
            out.append((code, round(best, 5)))
        return out

    def _step_response(self) -> tuple[float, float]:
        """Задержка до первого движения в кадре и τ разгона — на коде STEP_CODE."""
        self.progress("Замер разгона камеры…")
        ref = self.source.latest()
        threshold = 1.5 * ref.image.shape[1] / 1280
        t_cmd = time.monotonic()
        self.pacer.send(P.pan_tilt_speed(STEP_CODE, 50))
        t_first = wait_for_motion(self.source, t_cmd, threshold_px=threshold, reference=ref.image)
        samples = []  # (t, скорость px/с) по соседним кадрам
        prev = self.source.latest()
        t_prev = time.monotonic()
        while time.monotonic() - t_cmd < 2.2:
            fr = self.source.latest()
            if fr is not None and fr.seq != getattr(prev, "seq", None):
                now = time.monotonic()
                dx, _ = measure_shift(prev.image, fr.image)
                samples.append((now, abs(dx) / max(1e-3, now - t_prev)))
                prev, t_prev = fr, now
            time.sleep(0.005)
        self.pacer.send(P.STOP_PT)
        time.sleep(1.5)
        self.pacer.send(P.pan_tilt_speed(100 - STEP_CODE, 50))  # вернуть
        time.sleep(2.2)
        self.pacer.send(P.STOP_PT)
        time.sleep(1.2)
        if t_first is None or len(samples) < 6:
            d = default_calibration()
            return d.video_latency.get(self.stream_kind, 0.15), d.tau
        # сгладить по 3 кадрам
        sm = [(samples[i][0], statistics.mean(v for _, v in samples[i - 1 : i + 2])) for i in range(1, len(samples) - 1)]
        steady = statistics.mean(v for _, v in sm[-max(3, len(sm) // 4) :])
        t63 = next((t for t, v in sm if v >= 0.63 * steady), None)
        dead = default_calibration().dead_time
        latency = min(1.5, max(0.0, (t_first - t_cmd) - dead))
        tau = default_calibration().tau if t63 is None else min(1.0, max(0.1, t63 - t_first))
        return latency, tau

    def _zoom_rates(self) -> list[tuple[int, float]]:
        rates = []
        for code in ZOOM_CODES:
            self.progress(f"Скорость зума {code}…")
            before = self.source.latest().image
            self.pacer.send(P.zoom_speed(code))
            time.sleep(ZOOM_RUN_S)
            self.pacer.send(P.STOP_ZOOM)
            time.sleep(1.5)  # дождаться полной остановки: тогда сдвиг = скорость × время
            after = self.source.latest().image
            scale = estimate_scale(before, after)
            if scale and scale > 1.0:
                rates.append((code, round(math.log(scale) / ZOOM_RUN_S, 4)))
            self.pacer.send(P.zoom_speed(100 - code))
            time.sleep(ZOOM_RUN_S)
            self.pacer.send(P.STOP_ZOOM)
            time.sleep(1.2)
        return rates

    # --- прогон ---------------------------------------------------------

    def run(self) -> Calibration:
        start_pt = P.parse_pan_tilt(self.pacer.send(P.Q_PAN_TILT))
        start_zoom = P.parse_zoom(self.pacer.send(P.Q_ZOOM))
        zooms = self.zooms or default_zooms(start_zoom)
        try:
            curves = drop_flat_curves({z: self._pan_curve(z) for z in zooms})
            if not curves:
                raise RuntimeError(
                    "Слишком однотонная картинка — сдвиг кадра не измерить. "
                    "Наведите камеру на сцену с деталями и повторите."
                )
            self.pacer.send(P.zoom_abs(start_zoom))
            time.sleep(ZOOM_SETTLE_S)
            latency, tau = self._step_response()
            zoom_pts = self._zoom_rates() or default_calibration().zoom.points
        finally:
            self.pacer.try_send(P.STOP_PT)
            self.pacer.try_send(P.STOP_ZOOM)
            self.progress("Возвращаю камеру на место…")
            self.pacer.try_send(P.pan_tilt_abs(*start_pt))
            self.pacer.try_send(P.zoom_abs(start_zoom))
            time.sleep(3.0)
        base = default_calibration()
        video_latency = dict(base.video_latency)
        video_latency[self.stream_kind] = round(latency, 3)
        n = sum(len(c) for c in curves.values())
        self.progress(f"Готово: {n} замеров скорости")
        return Calibration(
            pan=SpeedTable(curves),
            zoom=ZoomTable(zoom_pts),
            dead_time=base.dead_time,
            tau=round(tau, 3),
            video_latency=video_latency,
            created=datetime.now().isoformat(timespec="seconds"),
        )
