"""Мастер калибровки: измеряет реальные скорости камеры (~3–4 минуты).

Камера ненадолго поворачивается в обе стороны на разных кодах скорости и зумах; перед каждым
пробегом она возвращается в исходную точку (#APC) и ждёт там остановки, зум — пока #GZ не перестанет
меняться. Скорость и разгон считаются по положению камеры (#APC, градусы) и переводятся в доли
кадра по углу обзора на этом зуме. Картинка нужна для задержки видео («камера сдвинулась → кадр
сдвинулся»), проверки шкалы и скорости зума. Итог — таблица «код → доли кадра в секунду», τ разгона,
задержка видео, скорость зума.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime
from typing import Callable

from ptztracker.camera import he42
from ptztracker.camera import protocol as P
from ptztracker.control.camera_model import Calibration, SpeedTable, ZoomTable, default_calibration
from ptztracker.diagnostics.motion import estimate_scale, measure_shift
from ptztracker.diagnostics.probe import Pacer
from ptztracker.video.frames import Frame, FrameSource

log = logging.getLogger(__name__)

DEFAULT_CODES = (53, 55, 57, 59, 61, 63, 65, 67, 70, 75, 80, 90)
FLAT_FW_S = 0.004  # быстрые коды дали меньше — камера по командам скорости не поворачивается
STEP_CODE = 70
STEP_RUN_S = 2.2
ZOOM_CODES = (60, 75, 90)
ZOOM_RUN_S = 0.6
PROBE_MAX_S, PROBE_MAX_FRACTION = 2.5, 0.9  # пробег: до 2,5 с или 0,9 кадра — разгон ~0,85 с
# Ожидание зума: #GZ не меняется ZOOM_STABLE_POLLS опросов подряд (~0,45 с). Было: всегда 2 с —
# возврат с теле длится дольше, и разгон мерился при едущем зуме (часть 0: задержка видео 0).
ZOOM_STABLE_POLLS = 3
ZOOM_SETTLE_TIMEOUT_S = 10.0
# Остановка в точке: два опроса #APC подряд отличаются не больше REST_UNITS и до цели ≤ ARRIVE_DEG.
REST_UNITS = 2
ARRIVE_DEG = 0.2
REST_TIMEOUT_S = 10.0
MIN_VIDEO_LATENCY_S = 0.05  # меньше — не замер, а сбой (часть 0: 0,0 вместо 0,15 с)
MOTION_THRESHOLD_PX = 1.5  # «кадр сдвинулся» — в пикселях кадра шириной 1280
LATENCY_SPAN = 0.05  # задержка видео — по кадрам, сдвинутым не дальше этой доли кадра
PICTURE_CHECK_TOL = 0.25  # картинка и #APC расходятся больше — предупредить о шкале
MONOTONE_TOL_ABS, MONOTONE_TOL_REL = 0.002, 0.05  # «скорость не падает с кодом» с этим допуском
TAU_GRID = [round(0.10 + 0.01 * i, 2) for i in range(91)]  # 0,10…1,00 с
T0_GRID_S = [0.01 * i for i in range(51)]  # начало движения: 0…0,5 с после команды

FLAT_SCENE_ERROR = (
    "Слишком однотонная картинка — по ней не видно, когда камера сдвинулась, и задержку видео "
    "не измерить. Наведите камеру на сцену с деталями и повторите."
)


def default_zooms(start_zoom: int) -> list[int]:
    """Широкий угол, текущий зум (рабочий) и тело — регулятор не должен экстраполировать."""
    return sorted({P.LENS_MIN, start_zoom, (start_zoom + P.LENS_MAX) // 2, P.LENS_MAX})


def hfov_at(zoom: int) -> float:
    """Угол обзора (°) при положении зума #GZ."""
    return he42.hfov_deg((zoom - P.LENS_MIN) / (P.LENS_MAX - P.LENS_MIN))


def drop_flat_curves(curves: dict[int, list[tuple[int, float]]]) -> dict[int, list[tuple[int, float]]]:
    """Отбросить зумы, где быстрые коды «стоят» (камера не поворачивалась)."""
    good = {}
    for z, curve in curves.items():
        fast = [v for c, v in curve if c >= 65]
        if fast and sum(1 for v in fast if v < FLAT_FW_S) <= len(fast) / 2:
            good[z] = curve
        else:
            log.warning("Зум %03X: камера не поворачивалась на быстрых кодах — замеры отброшены", z)
    return good


def monotone_curve(curve: list[tuple[int, float]]) -> tuple[list[tuple[int, float]], list[int]]:
    """Самая длинная цепочка точек, где скорость не падает с ростом кода (с допуском); остальные —
    выбросы. Возвращает (оставленные точки, коды выбросов). Было: «максимум предыдущих» — один
    выброс вверх растягивался на все следующие коды (часть 0: плато 0,024 fw/s на 53–59)."""
    pts = sorted(curve)
    n = len(pts)
    best, prev = [1] * n, [-1] * n
    for j in range(n):
        for i in range(j):
            vi = pts[i][1]
            if pts[j][1] >= vi - max(MONOTONE_TOL_ABS, MONOTONE_TOL_REL * vi) and best[i] + 1 > best[j]:
                best[j], prev[j] = best[i] + 1, i
    j = max(range(n), key=lambda k: best[k]) if n else -1
    keep = set()
    while j >= 0:
        keep.add(j)
        j = prev[j]
    return [p for k, p in enumerate(pts) if k in keep], [p[0] for k, p in enumerate(pts) if k not in keep]


def _ramp(s: float, tau: float) -> float:
    """Путь инерционного звена с единичной скоростью через s секунд после начала движения."""
    return s - tau * (1.0 - math.exp(-s / tau)) if s > 0 else 0.0


def fit_ramp(
    samples: list[tuple[float, float]], t_cmd: float, tau: float | None = None
) -> tuple[float, float, float]:
    """Подогнать путь x(t) = v·(s − τ·(1 − e^(−s/τ))), s = t − t0, к замерам (t, x).

    Возвращает (v, τ, t0). τ ищется по сетке TAU_GRID (или задан), t0 — от t_cmd до t_cmd + 0,5 с,
    v — наименьшими квадратами при данных τ и t0.
    """
    best = (math.inf, 0.0, tau or he42.RAMP_TAU_S, t_cmd)
    for tc in [tau] if tau else TAU_GRID:
        for dt0 in T0_GRID_S:
            t0 = t_cmd + dt0
            g = [_ramp(t - t0, tc) for t, _ in samples]
            gg = sum(x * x for x in g)
            if gg <= 0:
                continue
            v = sum(gi * x for gi, (_, x) in zip(g, samples)) / gg
            sse = sum((x - v * gi) ** 2 for gi, (_, x) in zip(g, samples))
            if sse < best[0]:
                best = (sse, v, tc, t0)
    return best[1], best[2], best[3]


def _time_to_travel(dist: float, v: float, tau: float) -> float:
    """Через сколько секунд после начала движения камера проедет dist (той же единицы, что v·с)."""
    lo, hi = 0.0, 5.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if abs(v) * _ramp(mid, tau) < dist:
            lo = mid
        else:
            hi = mid
    return hi


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

    # --- положение камеры -------------------------------------------------

    def _pan_tilt(self) -> tuple[int, int, float]:
        body, t = self.pacer.timed_send(P.Q_PAN_TILT)
        p, tl = P.parse_pan_tilt(body)
        return p, tl, t

    def _wait_zoom_settled(self) -> int:
        """Ждать, пока #GZ перестанет меняться; вернуть положение зума."""
        deadline = time.monotonic() + ZOOM_SETTLE_TIMEOUT_S
        last, same = None, 0
        while True:
            z = P.parse_zoom(self.pacer.send(P.Q_ZOOM))
            same = same + 1 if last is not None and abs(z - last) <= 1 else 0
            last = z
            if same >= ZOOM_STABLE_POLLS - 1:
                return z
            if time.monotonic() > deadline:
                self.progress("Внимание: зум долго не останавливается — продолжаю.")
                return z

    def _wait_at_rest(self, target: tuple[int, int] | None = None) -> tuple[int, int]:
        """Ждать, пока камера остановится (и, если задано, доедет до target); вернуть положение #APC."""
        arrive = ARRIVE_DEG / he42.APC_DEG_PER_UNIT
        deadline = time.monotonic() + REST_TIMEOUT_S
        last = None
        while True:
            p, t, _ = self._pan_tilt()
            still = last is not None and abs(p - last[0]) <= REST_UNITS and abs(t - last[1]) <= REST_UNITS
            there = target is None or (abs(p - target[0]) <= arrive and abs(t - target[1]) <= arrive)
            last = (p, t)
            if still and there:
                return last
            if time.monotonic() > deadline:
                self.progress("Внимание: камера долго не встаёт на место — продолжаю.")
                return last

    def _go_to(self, start: tuple[int, int]) -> None:
        self.pacer.send(P.pan_tilt_abs(*start))
        self._wait_at_rest(start)

    def _frame(self) -> Frame:
        fr = self.source.latest()
        if fr is None:
            raise RuntimeError("Нет видео с камеры — калибровка невозможна")
        return fr

    # --- пробег ------------------------------------------------------------

    def _run(self, code: int, hfov: float, max_s: float, snapshots: tuple[float, ...] = ()) -> dict:
        """Пробег с кодом code из состояния покоя: замеры #APC (t, градусы от начала) до max_s или
        PROBE_MAX_FRACTION кадра, затем стоп. snapshots — через сколько секунд после команды взять кадры."""
        p0, _, _ = self._pan_tilt()
        max_deg = PROBE_MAX_FRACTION * hfov
        _, t_cmd = self.pacer.timed_send(P.pan_tilt_speed(code, 50))
        samples, frames, pending = [], [], list(snapshots)
        try:
            while time.monotonic() - t_cmd < max_s:
                p, _, t = self._pan_tilt()
                x = (p - p0) * he42.APC_DEG_PER_UNIT
                samples.append((t, x))
                while pending and t - t_cmd >= pending[0]:
                    pending.pop(0)
                    frames.append(self._frame())
                if abs(x) >= max_deg:
                    break
        finally:
            self.pacer.send(P.STOP_PT)
        return {"t_cmd": t_cmd, "samples": samples, "frames": frames}

    def _step_response(self, hfov: float) -> tuple[float, float]:
        """Задержка видео и τ разгона на коде STEP_CODE; камера в покое, зум доехал."""
        self.progress("Замер разгона и задержки видео…")
        ref = self._frame()
        width = ref.image.shape[1]
        threshold = MOTION_THRESHOLD_PX * width / 1280
        shifts: list[tuple[float, float]] = []  # (момент получения кадра, сдвиг от опорного, px)
        done = threading.Event()
        watcher = threading.Thread(target=self._watch_shifts, args=(ref, shifts, done), name="calib-motion", daemon=True)
        watcher.start()
        try:
            run = self._run(STEP_CODE, hfov, STEP_RUN_S, snapshots=(1.2, 1.6))
        finally:
            done.set()
            watcher.join()
        moved = [(t, d) for t, d in shifts if d > threshold]
        if not moved:
            raise RuntimeError(FLAT_SCENE_ERROR)
        v, tau, t0 = fit_ramp(run["samples"], run["t_cmd"])
        # Каждый сдвинутый кадр показывает камеру в положении d (в градусах — по углу обзора у центра
        # кадра); по #APC известно, когда она там была. Берём первые кадры движения (до LATENCY_SPAN
        # кадра): там ошибка шкалы мало сдвигает время, а сам кадр не опаздывает на период до порога.
        early = [(t, d) for t, d in moved if d <= LATENCY_SPAN * width] or moved[:1]
        lat = sorted(t - (t0 + _time_to_travel(d / width * hfov, v, tau)) for t, d in early)
        latency = lat[len(lat) // 2]
        log.info(
            "Разгон: v=%.2f°/с τ=%.3f с, начало через %.3f с; задержка видео %.3f с (%d кадров)",
            v, tau, t0 - run["t_cmd"], latency, len(lat),
        )
        default = default_calibration().latency_for(self.stream_kind)
        if latency < MIN_VIDEO_LATENCY_S:
            self.progress(
                f"Внимание: задержка видео получилась {latency:.2f} с — так не бывает. "
                f"Беру обычную для этого потока: {default:.2f} с."
            )
            latency = default
        latency = min(1.5, latency)
        self._check_picture(run, v, tau, t0, latency, hfov, width)
        return latency, tau

    def _watch_shifts(self, ref: Frame, out: list[tuple[float, float]], done: threading.Event) -> None:
        """Сдвиг каждого нового кадра от опорного, пока кадр не уедет на LATENCY_SPAN (или до done)."""
        width, last = ref.image.shape[1], ref.seq
        while not done.is_set():
            fr = self.source.latest()
            if fr is not None and fr.seq != last:
                last = fr.seq
                dx, dy = measure_shift(ref.image, fr.image)
                d = math.hypot(dx, dy)
                out.append((fr.t_capture, d))
                if d > 2 * LATENCY_SPAN * width:
                    return
            time.sleep(0.005)

    def _check_picture(self, run: dict, v: float, tau: float, t0: float, latency: float, hfov: float, width: int):
        """Сверить сдвиг кадра с поворотом по #APC: расхождение — шкала положения или угол обзора не те."""
        if len(run["frames"]) < 2:
            return
        a, b = run["frames"][:2]
        if b.seq == a.seq:
            return
        dx, _ = measure_shift(a.image, b.image)
        pic = abs(dx) / width
        # кадр снят на latency раньше, чем пришёл
        apc = abs(v) * (_ramp(b.t_capture - latency - t0, tau) - _ramp(a.t_capture - latency - t0, tau)) / hfov
        if apc <= 0:
            return
        log.info("Проверка по картинке: %.4f кадра, по положению %.4f", pic, apc)
        if abs(pic / apc - 1) > PICTURE_CHECK_TOL:
            self.progress(
                f"Внимание: по картинке камера повернулась на {pic / apc:.0%} от того, что сообщает сама. "
                "Скорости могут быть неточными — пришлите отчёт «Диагностика камеры»."
            )

    def _pan_curve(self, zoom: int, start: tuple[int, int], tau: float) -> list[tuple[int, float]]:
        self.pacer.send(P.zoom_abs(zoom))
        hfov = hfov_at(self._wait_zoom_settled())
        curve = []
        for i, code in enumerate(self.codes):
            c = code if i % 2 == 0 else 100 - code  # попеременно вправо/влево
            self._go_to(start)
            self.progress(f"Зум {zoom:03X}: скорость {code}…")
            run = self._run(c, hfov, PROBE_MAX_S)
            v, _, _ = fit_ramp(run["samples"], run["t_cmd"], tau=tau)
            curve.append((code, round(abs(v) / hfov, 5)))  # град/с → доли кадра (у центра кадра)
        kept, dropped = monotone_curve(curve)
        if dropped:
            log.warning("Зум %03X: выбросы на кодах %s отброшены", zoom, dropped)
        return kept

    def _zoom_rates(self) -> list[tuple[int, float]]:
        rates = []
        for code in ZOOM_CODES:
            self.progress(f"Скорость зума {code}…")
            before = self._frame().image
            self.pacer.send(P.zoom_speed(code))
            time.sleep(ZOOM_RUN_S)
            self.pacer.send(P.STOP_ZOOM)
            time.sleep(1.5)  # дождаться полной остановки: тогда сдвиг = скорость × время
            after = self._frame().image
            scale = estimate_scale(before, after)
            if scale and scale > 1.0:
                rates.append((code, round(math.log(scale) / ZOOM_RUN_S, 4)))
            self.pacer.send(P.zoom_speed(100 - code))
            time.sleep(ZOOM_RUN_S)
            self.pacer.send(P.STOP_ZOOM)
            time.sleep(1.2)
        return rates

    # --- прогон ---------------------------------------------------------

    def _restore(self, start: tuple[int, int], start_zoom: int) -> None:
        self.pacer.try_send(P.STOP_PT)
        self.pacer.try_send(P.STOP_ZOOM)
        self.progress("Возвращаю камеру на место…")
        self.pacer.try_send(P.pan_tilt_abs(*start))
        self.pacer.try_send(P.zoom_abs(start_zoom))
        try:
            self._wait_at_rest(start)
            self._wait_zoom_settled()
        except Exception as e:  # связь пропала — камера доедет сама
            log.warning("Возврат камеры: %s", e)
            time.sleep(3.0)

    def run(self) -> Calibration:
        start_zoom = self._wait_zoom_settled()
        start = self._wait_at_rest()
        zooms = self.zooms or default_zooms(start_zoom)
        try:
            latency, tau = self._step_response(hfov_at(start_zoom))
            curves = drop_flat_curves({z: self._pan_curve(z, start, tau) for z in zooms})
            if not curves:
                raise RuntimeError(
                    "Камера не поворачивается по командам скорости. Проверьте, что её не держит пульт, и повторите."
                )
            self.pacer.send(P.zoom_abs(start_zoom))
            self._wait_zoom_settled()
            self._go_to(start)
            zoom_pts = self._zoom_rates() or default_calibration().zoom.points
        finally:
            self._restore(start, start_zoom)
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
