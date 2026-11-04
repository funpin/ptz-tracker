"""Мастер калибровки: измеряет реальные скорости камеры (~3 минуты).

Камера ненадолго поворачивается на разных кодах скорости и зумах (и наклоняется вверх и вниз); перед каждым
пробегом она возвращается в исходную точку (#APC) и ждёт там остановки, зум — пока #GZ не перестанет
меняться. Скорость и разгон считаются по положению камеры (#APC, градусы) и переводятся в доли
кадра по углу обзора на этом зуме. Картинка нужна для задержки видео («камера сдвинулась → кадр
сдвинулся»), проверки шкалы и скорости зума. Итог — таблица «код → доли кадра в секунду», наклон к повороту,
τ разгона, задержка видео, скорость зума и сведения о камере (модель, прошивка, поток, зумы замеров).
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
from ptztracker.control.framing import TILT_FROM_FH
from ptztracker.diagnostics.motion import estimate_scale, measure_shift
from ptztracker.diagnostics.probe import Pacer
from ptztracker.video.frames import Frame, FrameSource

log = logging.getLogger(__name__)

DEFAULT_CODES = (53, 55, 57, 59, 61, 63, 65, 67, 70, 75, 80, 90)
# Тело (#GZ выше he42.TELE_START_GZ): поворот там замедлен (HE40 2026-10-30), регулятор ведёт до 0xD55. Мастер
# меряет точки TELE_ZOOMS и 0xFFF по #APC несколькими кодами TELE_CODES; быстрее последнего кода — по форме
# паспортной кривой от замера (he42 держит долю замедления кода 75). Пробег на теле — до TELE_PROBE_MAX_S и
# до PROBE_MAX_FRACTION кадра РАБОЧЕГО зума (с которого начат мастер): полкадра теле (2–3°) код 75 проходит за
# 0,1–0,2 с — это 1–2 опроса #APC; полкадра рабочего плана дают 0,5–1,5 с и камера не уходит со сцены.
TELE_ZOOMS = (0xD55, 0xEA9)
TELE_CODES = (55, 63, 70, 75)
TELE_PROBE_MAX_S = 1.5
# Наклон: коды TILT_CODES вверх и вниз, короткие пробеги (до PROBE_MAX_FRACTION высоты кадра) на рабочем зуме.
TILT_CODES = (63, 75)
FLAT_FW_S = 0.004  # быстрые коды дали меньше — камера по командам скорости не поворачивается
STEP_CODE = 70
STEP_RUN_S = 2.2
ZOOM_CODES = (60, 75, 90)
ZOOM_RUN_S = 0.6
# Пробег: до 2,5 с или PROBE_MAX_FRACTION кадра от исходной точки, затем стоп. Живая HE42 (2026-10-29): мастер увёл
# pan на ~32° (больше полкадра 62°); пол-кадра — жёсткая граница, 0,4 оставляет запас на инерцию после стопа.
# STOP_LAG_S: пока стоп дойдёт и камера затормозит (опрос #APC + 0,3 с торможения), она проедет ещё v·STOP_LAG_S:
# стоп шлём заранее, когда x + v·STOP_LAG_S дошло до предела (на быстрых кодах и теле за один опрос — пол-кадра).
PROBE_MAX_S, PROBE_MAX_FRACTION, STOP_LAG_S = 2.5, 0.4, 0.45
# Прогноз стопа — только при угле обзора ≥ PREDICT_MIN_HFOV (на теле быстрый код пролетает полкадра за опрос:
# ранний стоп оставляет 2–3 замера и скорость выходит втрое заниженной) и после MIN_FIT_SAMPLES опросов.
MIN_FIT_SAMPLES, PREDICT_MIN_HFOV = 3, 20.0
# До MIN_FIT_SAMPLES опросов скорость неизвестна, а при разгоне 0,3 с (2026-10-29) камера на коде 90 (61°/с) за три
# опроса уже у предела пробега: прогноз берёт долю PRIOR_SPEED_FRACTION от паспортной скорости кода (he42.PAN_DEG_S).
PRIOR_SPEED_FRACTION = 0.7
# Ожидание зума: #GZ не дальше ZOOM_ARRIVE_UNITS от заданного и не меняется ZOOM_STABLE_POLLS опросов
# подряд (~0,45 с). Было: всегда 2 с — возврат с теле длится дольше, и разгон мерился при едущем зуме
# (часть 0: задержка видео 0). Без цели «стоит» и до начала движения, если камера тронулась не сразу.
ZOOM_STABLE_POLLS = 3
ZOOM_ARRIVE_UNITS = 0x20
ZOOM_SETTLE_TIMEOUT_S = 8.0
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

CANCEL_POLL_S = 0.1  # отмена проверяется не реже (в паузах мастера и в каждом опросе пробега)


class CalibrationCancelled(RuntimeError):
    """Оператор прервал мастер: камера остановлена и возвращена на место, калибровка не сохраняется."""

    def __init__(self) -> None:
        super().__init__("Калибровка прервана — камера возвращена на место, прежние настройки не изменились.")


FLAT_SCENE_WARNING = (
    "Картинка однотонная: задержка видео не измерена, взято типовое значение для этого потока. "
    "Для точной настройки наведите камеру на сцену с деталями и повторите."
)


def default_zooms(start_zoom: int) -> list[int]:
    """Широкий угол, текущий зум (рабочий), середина до тела, точки тела и 0xFFF — регулятор не должен
    экстраполировать (было до 2026-11-05: без 0xD55 и 0xEA9 — между AAA и FFF таблица занижала камеру)."""
    return sorted({P.LENS_MIN, start_zoom, (start_zoom + P.LENS_MAX) // 2, *TELE_ZOOMS, P.LENS_MAX})


def is_tele(zoom: int) -> bool:
    return zoom > he42.TELE_START_GZ


def extend_tele_curve(curve: list[tuple[int, float]], zoom: int, codes: tuple[int, ...]) -> list[tuple[int, float]]:
    """Коды codes быстрее последнего замеренного на теле — по форме he42.pan_deg_s от этого замера (камера на
    теле быстрые коды за полкадра рабочего плана не разгоняет). Без этого таблица держала бы скорость последнего
    кода до 99, а камера на 99 — в разы быстрее: регулятор раскачал бы её."""
    if not curve:
        return curve
    top, v_top = max(curve)
    deg_top = he42.pan_deg_s(top, zoom)
    if deg_top <= 0:
        return curve
    extra = [(c, round(v_top * he42.pan_deg_s(c, zoom) / deg_top, 5)) for c in codes if c > top]
    return sorted(curve + extra)


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
    # Допуск — к каждому соседнему шагу, поэтому цепочка из многих точек может «сползти» вниз на
    # несколько допусков. Это сознательно: допуск мал (0,002 fw/s или 5%), а шум #APC на медленных
    # кодах — того же порядка; цель — отбросить грубый выброс, а не выровнять кривую.
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


def tilt_ratio(tilt_deg: dict[int, float], pan: SpeedTable, zoom: int) -> list[tuple[int, float]] | None:
    """Наклон ÷ поворот по коду на зуме замера наклона (поворот — по таблице мастера); None — нечем делить."""
    hfov = hfov_at(zoom)
    out = []
    for code, deg in sorted(tilt_deg.items()):
        pan_deg = pan.speed(code, zoom) * hfov
        if pan_deg > 0 and deg > 0:
            out.append((code, round(deg / pan_deg, 3)))
    return out or None


class _Sender:
    """Pacer поверх произвольной функции отправки (клиент сессии или отдельный); команды (не запросы
    положения) — в on_cmd."""

    def __init__(self, send: Callable[[str], str], on_cmd: Callable[[str], None] | None = None):
        self._send, self._on_cmd = send, on_cmd

    def send(self, cmd: str) -> str:
        if self._on_cmd is not None and cmd not in (P.Q_PAN_TILT, P.Q_ZOOM):
            self._on_cmd(cmd)
        return self._send(cmd)


def _nominal_deg_s(code: int, zoom: int | None = None) -> float:
    """Паспортная скорость кода, °/с (линейно между точками he42.PAN_DEG_S; для кода < 50 — по модулю); с zoom —
    с замедлением на теле (he42.tele_pan_ratio)."""
    n = 50 + abs(code - 50)
    pts = sorted(he42.PAN_DEG_S.items())
    for (c0, v0), (c1, v1) in zip(pts, pts[1:], strict=False):
        if c0 <= n <= c1:
            v = v0 + (v1 - v0) * (n - c0) / (c1 - c0)
            return v * he42.tele_pan_ratio(n, zoom) if zoom is not None else v
    return 0.0


class CalibrationRunner:
    def __init__(
        self,
        client_send: Callable[[str], str],
        source: FrameSource,
        progress: Callable[[str], None],
        zooms: list[int] | None = None,
        codes: tuple[int, ...] = DEFAULT_CODES,
        stream_kind: str = "mjpeg",
        model: str | None = None,
        firmware: str | None = None,
        tele_codes: tuple[int, ...] | None = None,
        tilt_codes: tuple[int, ...] = TILT_CODES,
        cancel: threading.Event | None = None,
        on_sample: Callable[[str, dict], None] | None = None,
    ):
        # cancel — оператор прервал: стоп, камера домой, CalibrationRunner.run бросает CalibrationCancelled.
        # on_sample(kind, data) — наблюдатель (окно мастера): "apc" {t, pan, tilt, zoom}, "cmd" {t, cmd},
        # "step" {index, total, title}, "result" {name, …}. Его ошибки мастер не прерывают.
        self.cancel = cancel
        self.on_sample = on_sample
        self._restoring = False
        self._zoom_pos: int | None = None
        self.pacer = Pacer(_Sender(client_send, lambda c: self._emit("cmd", {"t": time.monotonic(), "cmd": c})))
        self.source = source
        self.progress = progress
        self.zooms = zooms
        self.codes = codes
        # на теле — TELE_CODES при кодах по умолчанию; заданные коды (проверки) — те же на всех зумах
        self.tele_codes = tele_codes or (TELE_CODES if codes == DEFAULT_CODES else codes)
        self.tilt_codes = tilt_codes
        self.stream_kind = stream_kind
        self.model = model
        self.firmware = firmware

    # --- отмена и наблюдатель ---------------------------------------------

    def _emit(self, kind: str, data: dict) -> None:
        """Передать замер наблюдателю; его ошибка — только в лог (как у записи: наблюдение не мешает)."""
        if self.on_sample is None:
            return
        try:
            self.on_sample(kind, data)
        except Exception as e:
            log.warning("Наблюдатель мастера (%s): %s", kind, e)

    def _check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set() and not self._restoring:
            raise CalibrationCancelled()

    def _sleep(self, s: float) -> None:
        """Пауза с проверкой отмены каждые CANCEL_POLL_S."""
        end = time.monotonic() + s
        while (left := end - time.monotonic()) > 0:
            self._check_cancel()
            time.sleep(min(CANCEL_POLL_S, left))
        self._check_cancel()

    # --- положение камеры -------------------------------------------------

    def _pan_tilt(self) -> tuple[int, int, float]:
        self._check_cancel()
        body, t = self.pacer.timed_send(P.Q_PAN_TILT)
        p, tl = P.parse_pan_tilt(body)
        self._emit("apc", {"t": t, "pan": p, "tilt": tl, "zoom": self._zoom_pos})
        return p, tl, t

    def _wait_zoom_settled(self, target: int | None = None) -> int:
        """Ждать, пока #GZ доедет до target (если задан) и перестанет меняться; вернуть положение зума."""
        deadline = time.monotonic() + ZOOM_SETTLE_TIMEOUT_S
        last, same = None, 0
        while True:
            self._check_cancel()
            z = self._zoom_pos = P.parse_zoom(self.pacer.send(P.Q_ZOOM))
            same = same + 1 if last is not None and abs(z - last) <= 1 else 0
            last = z
            there = target is None or abs(z - target) <= ZOOM_ARRIVE_UNITS
            if there and same >= ZOOM_STABLE_POLLS - 1:
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

    @staticmethod
    def _toward_centre(code: int, start: tuple[int, int]) -> int:
        """Код pan (51–99) в сторону центра диапазона от start: пробег до 0,9 кадра не упрётся в край.
        Было: попеременно вправо/влево — у края диапазона пробег упирался и давал заниженную скорость."""
        return code if start[0] <= he42.APC_CENTER else 100 - code

    def _frame(self) -> Frame:
        fr = self.source.latest()
        if fr is None:
            raise RuntimeError("Нет видео с камеры — калибровка невозможна")
        return fr

    # --- пробег ------------------------------------------------------------

    def _run(
        self, code: int, hfov: float, max_s: float, snapshots: tuple[float, ...] = (),
        tilt: bool = False, max_deg: float | None = None, zoom: int | None = None,
    ) -> dict:
        """Пробег с кодом code из состояния покоя: замеры #APC (t, градусы от начала) до max_s или max_deg
        (по умолчанию PROBE_MAX_FRACTION кадра), затем стоп. snapshots — через сколько секунд после команды взять
        кадры. tilt — пробег наклоном; zoom — для паспортной скорости на теле."""
        axis = 1 if tilt else 0
        x0 = self._pan_tilt()[axis]
        max_deg = max_deg or PROBE_MAX_FRACTION * hfov
        _, t_cmd = self.pacer.timed_send(P.pan_tilt_speed(50, code) if tilt else P.pan_tilt_speed(code, 50))
        samples, frames, pending = [], [], list(snapshots)
        try:
            while time.monotonic() - t_cmd < max_s:
                pt = self._pan_tilt()
                t = pt[2]
                x = (pt[axis] - x0) * he42.APC_DEG_PER_UNIT
                samples.append((t, x))
                while pending and t - t_cmd >= pending[0]:
                    pending.pop(0)
                    frames.append(self._frame())
                if abs(x) >= max_deg:
                    break
                if max_deg >= PROBE_MAX_FRACTION * PREDICT_MIN_HFOV:
                    v = PRIOR_SPEED_FRACTION * _nominal_deg_s(code, zoom)
                    if len(samples) >= MIN_FIT_SAMPLES:
                        (ta, xa), (tb, xb) = samples[-2], samples[-1]
                        if tb > ta:
                            v = max(v, abs((xb - xa) / (tb - ta)))
                    if abs(x) + v * STOP_LAG_S >= max_deg:
                        break
        finally:
            self.pacer.send(P.STOP_PT)
        return {"t_cmd": t_cmd, "samples": samples, "frames": frames}

    def _step_response(self, hfov: float, start: tuple[int, int]) -> tuple[float, float]:
        """Задержка видео и τ разгона на коде STEP_CODE; камера в покое, зум доехал."""
        self.progress("Замер разгона и задержки видео…")
        ref = self._frame()
        width = ref.image.shape[1]
        threshold = MOTION_THRESHOLD_PX * width / 1280
        shifts: list[tuple[float, float]] = []  # (момент получения кадра, сдвиг от опорного, px)
        done, failed = threading.Event(), threading.Event()
        watcher = threading.Thread(
            target=self._watch_shifts, args=(ref, shifts, done, failed), name="calib-motion", daemon=True
        )
        watcher.start()
        try:
            run = self._run(self._toward_centre(STEP_CODE, start), hfov, STEP_RUN_S, snapshots=(1.2, 1.6))
        finally:
            done.set()
            watcher.join()
        v, tau, t0 = fit_ramp(run["samples"], run["t_cmd"])
        default = default_calibration().latency_for(self.stream_kind)
        moved = [(t, d) for t, d in shifts if d > threshold]
        if failed.is_set():
            self.progress(
                "Внимание: картинку не удалось обработать — задержка видео не измерена, "
                f"взято типовое значение для этого потока ({default:.2f} с)."
            )
            return default, tau
        if not moved:
            self.progress("Внимание: " + FLAT_SCENE_WARNING)
            return default, tau
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
        if latency < MIN_VIDEO_LATENCY_S:
            self.progress(
                f"Внимание: задержка видео получилась {latency:.2f} с — так не бывает. "
                f"Беру обычную для этого потока: {default:.2f} с."
            )
            latency = default
        latency = min(1.5, latency)
        self._check_picture(run, v, tau, t0, latency, hfov, width)
        return latency, tau

    def _watch_shifts(
        self, ref: Frame, out: list[tuple[float, float]], done: threading.Event, failed: threading.Event
    ) -> None:
        """Сдвиг каждого нового кадра от опорного, пока кадр не уедет на LATENCY_SPAN (или до done).
        Сбой обработки кадра — failed (а не «картинка однотонная»)."""
        width, last = ref.image.shape[1], ref.seq
        while not done.is_set():
            fr = self.source.latest()
            if fr is not None and fr.seq != last:
                last = fr.seq
                try:
                    dx, dy = measure_shift(ref.image, fr.image)
                except Exception as e:
                    log.warning("Сдвиг кадра не измерен: %s", e)
                    failed.set()
                    return
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

    def _pan_curve(
        self, zoom: int, start: tuple[int, int], tau: float, scene_hfov: float
    ) -> list[tuple[int, float]]:
        """Кривая «код → fw/s» на зуме zoom. scene_hfov — угол обзора рабочего зума: на теле пробег до
        PROBE_MAX_FRACTION его кадра (см. TELE_CODES)."""
        self._go_to(start)  # сначала домой на прежнем зуме: на теле полкадра — это уже другой угол
        self.pacer.send(P.zoom_abs(zoom))
        gz = self._wait_zoom_settled(zoom)
        hfov = hfov_at(gz)  # угол — по фактическому #GZ
        tele = is_tele(zoom)
        codes, max_s = (self.tele_codes, TELE_PROBE_MAX_S) if tele else (self.codes, PROBE_MAX_S)
        max_deg = PROBE_MAX_FRACTION * max(hfov, scene_hfov) if tele else None
        curve = []
        for code in codes:
            c = self._toward_centre(code, start)
            self._go_to(start)
            self.progress(f"Зум {zoom:03X}: скорость {code}…")
            run = self._run(c, hfov, max_s, max_deg=max_deg, zoom=gz)
            v, _, _ = fit_ramp(run["samples"], run["t_cmd"], tau=tau)
            curve.append((code, round(abs(v) / hfov, 5)))  # град/с → доли кадра (у центра кадра)
        kept, dropped = monotone_curve(curve)
        if dropped:
            log.warning("Зум %03X: выбросы на кодах %s отброшены", zoom, dropped)
        return extend_tele_curve(kept, gz, self.codes) if tele else kept

    def _emit_pan_result(self, zoom: int, curve: list[tuple[int, float]]) -> None:
        """Наблюдателю: °/с камеры на этом зуме по кодам рядом с таблицей без калибровки (he42.pan_deg_s)."""
        hfov = hfov_at(zoom)
        self._emit("result", {
            "name": "pan", "zoom": zoom,
            "codes": [[c, round(v * hfov, 3), round(he42.pan_deg_s(c, zoom), 3)] for c, v in curve],
        })

    def _tilt_deg_s(self, start: tuple[int, int], hfov: float, tau: float) -> dict[int, float]:
        """Наклон, °/с, на кодах tilt_codes: пробег вверх и вниз из исходной точки (короткий — до
        PROBE_MAX_FRACTION высоты кадра), между ними — домой. Берётся больший из двух: упор в край диапазона
        или тяжесть камеры только замедляют, а «камера медленнее модели» для регулятора безопасна."""
        out = {}
        for code in self.tilt_codes:
            speeds = []
            for c in (code, 100 - code):
                self._go_to(start)
                self.progress(f"Наклон: скорость {code}…")
                run = self._run(c, hfov, PROBE_MAX_S, tilt=True, max_deg=PROBE_MAX_FRACTION * hfov * TILT_FROM_FH)
                v, _, _ = fit_ramp(run["samples"], run["t_cmd"], tau=tau)
                speeds.append(abs(v))
            out[code] = max(speeds)
            log.info("Наклон %d: %.2f / %.2f °/с (вверх / вниз)", code, *speeds)
        return out

    def _zoom_rates(self) -> list[tuple[int, float]]:
        rates = []
        for code in ZOOM_CODES:
            self.progress(f"Скорость зума {code}…")
            before = self._frame().image
            self.pacer.send(P.zoom_speed(code))
            try:
                self._sleep(ZOOM_RUN_S)
            finally:
                self.pacer.send(P.STOP_ZOOM)
            self._sleep(1.5)  # дождаться полной остановки: тогда сдвиг = скорость × время
            after = self._frame().image
            scale = estimate_scale(before, after)
            if scale and scale > 1.0:
                rates.append((code, round(math.log(scale) / ZOOM_RUN_S, 4)))
            self.pacer.send(P.zoom_speed(100 - code))
            try:
                self._sleep(ZOOM_RUN_S)
            finally:
                self.pacer.send(P.STOP_ZOOM)
            self._sleep(1.2)
        return rates

    # --- прогон ---------------------------------------------------------

    def _restore(self, start: tuple[int, int], start_zoom: int) -> None:
        self._restoring = True  # возврат домой отмена не прерывает
        self.pacer.try_send(P.STOP_PT)
        self.pacer.try_send(P.STOP_ZOOM)
        self.progress("Возвращаю камеру на место…")
        self.pacer.try_send(P.pan_tilt_abs(*start))
        self.pacer.try_send(P.zoom_abs(start_zoom))
        try:
            self._wait_at_rest(start)
            self._wait_zoom_settled(start_zoom)
        except Exception as e:  # связь пропала — камера доедет сама
            log.warning("Возврат камеры: %s", e)
            time.sleep(3.0)

    def run(self) -> Calibration:
        """Прогон мастера. Отмена (cancel) — CalibrationCancelled после возврата камеры на место."""
        self._restoring = False
        start_zoom = self._wait_zoom_settled()
        start = self._wait_at_rest()
        zooms = self.zooms or default_zooms(start_zoom)
        scene_hfov = hfov_at(start_zoom)
        titles = ["Разгон и задержка видео", "Наклон", *(f"Поворот на зуме {z:03X}" for z in zooms),
                  "Скорость зума", "Возврат на место"]
        step = iter(range(len(titles)))

        def next_step() -> None:
            self._check_cancel()
            i = next(step)
            self._emit("step", {"index": i, "total": len(titles), "title": titles[i]})

        try:
            next_step()
            latency, tau = self._step_response(scene_hfov, start)
            self._emit("result", {"name": "tau", "value": round(tau, 3)})
            self._emit("result", {"name": "video_latency", "value": round(latency, 3), "stream": self.stream_kind})
            next_step()
            tilt_deg = self._tilt_deg_s(start, scene_hfov, tau)  # на рабочем зуме, пока он не менялся
            raw = {}
            for z in zooms:
                next_step()
                raw[z] = self._pan_curve(z, start, tau, scene_hfov)
                self._emit_pan_result(z, raw[z])
            curves = drop_flat_curves(raw)
            if not curves:
                raise RuntimeError(
                    "Камера не поворачивается по командам скорости. Проверьте, что её не держит пульт, и повторите."
                )
            next_step()
            self._go_to(start)  # домой до смены зума: на широком угле полкадра — больше, чем было на теле
            self.pacer.send(P.zoom_abs(start_zoom))
            self._wait_zoom_settled(start_zoom)
            zoom_pts = self._zoom_rates() or default_calibration().zoom.points
            self._emit("result", {"name": "zoom", "points": [list(p) for p in zoom_pts]})
        finally:
            self._emit("step", {"index": len(titles) - 1, "total": len(titles), "title": titles[-1]})
            self._restore(start, start_zoom)
        self._restoring = False
        self._check_cancel()  # отмена в самом конце (камера уже дома): калибровку всё равно не отдаём
        base = default_calibration()
        video_latency = dict(base.video_latency)
        video_latency[self.stream_kind] = round(latency, 3)
        n = sum(len(c) for c in curves.values())
        self.progress(f"Готово: {n} замеров скорости")
        pan = SpeedTable(curves)
        tilt = tilt_ratio(tilt_deg, pan, start_zoom)
        self._emit("result", {"name": "dead_time", "value": base.dead_time})
        self._emit("result", {"name": "tilt", "codes": [[c, round(tilt_deg[c], 3), r] for c, r in tilt or []]})
        return Calibration(
            pan=pan,
            zoom=ZoomTable(zoom_pts),
            dead_time=base.dead_time,
            tau=round(tau, 3),
            video_latency=video_latency,
            created=datetime.now().isoformat(timespec="seconds"),
            tilt=tilt,
            model=self.model,
            firmware=self.firmware,
            stream_kind=self.stream_kind,
            zoom_points=sorted(curves),
        )
