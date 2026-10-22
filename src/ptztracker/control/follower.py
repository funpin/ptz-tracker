"""Слежение: поток, который по результатам анализа ведёт камеру (pan/tilt/zoom).

Работает внутри CameraSession. Любое ручное действие выключает слежение
(сессия вызывает disable), поэтому автоматика и оператор не борются за камеру.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from typing import Callable

from ptztracker.camera import protocol as P
from ptztracker.camera.scheduler import CommandScheduler, Priority
from ptztracker.control.camera_model import Calibration
from ptztracker.control.composition import Composition, measure_body
from ptztracker.control.effort import EffortProfile
from ptztracker.control.framing import FramingController, FramingInput, FramingOutput
from ptztracker.recorder import NullRecorder, Recorder, safe_event

log = logging.getLogger(__name__)

LOOP_HZ = 25.0
STALE_S = 0.6  # результат анализа старше — анализ встал, камеру останавливаем
ZOOM_POLL_S = 0.5  # при слежении: зум и (для записи) положение #APC; чаще — опрос отнимает окна у команд
IDLE_POSITION_POLL_S = 2.0  # слежение выкл., идёт запись: положение и зум реже — не мешать ручному управлению
MANUAL_QUIET_S = 2.0  # после ручной команды столько не опрашиваем (запрос занимает окно ~130 мс)
TILT_FROM_FH = 9 / 16  # скорость по вертикали (доли высоты) → эквивалент по ширине кадра 16:9


def output_codes(
    calib: Calibration, out: FramingOutput, zoom_pos: int, current: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Скорости регулятора → коды pan/tilt/zoom камеры (с гистерезисом от текущих кодов).

    Общая для слежения и повтора записи: повтор должен давать те же коды, что ушли в камеру.
    """
    p0, t0, z0 = current
    pan = calib.pan.code_for(out.pan, zoom_pos, current=p0)
    tilt = calib.pan.code_for(out.tilt * TILT_FROM_FH, zoom_pos, current=t0)
    zc = calib.zoom.code_for(out.zoom, current=z0)
    return pan, tilt, zc


class Follower:
    def __init__(
        self,
        analyzer,
        scheduler: CommandScheduler,
        calib: Calibration,
        video_latency: Callable[[], float],
        composition: Composition,
        effort: EffortProfile,
        query_zoom: Callable[[], int | None],
        recorder: Recorder | None = None,
        query_pan_tilt: Callable[[], tuple[int, int] | None] | None = None,
    ):
        self.analyzer = analyzer
        self.scheduler = scheduler
        self.calib = calib
        self.video_latency = video_latency
        self.query_zoom = query_zoom
        self.query_pan_tilt = query_pan_tilt
        self.recorder: Recorder = recorder or NullRecorder()
        self.controller = FramingController(composition, effort, calib, video_latency())
        self.enabled = False
        self.last_reason: str | None = None
        self.output: FramingOutput | None = None
        self.on_change: Callable[[bool, str | None], None] | None = None
        self._lock = threading.Lock()
        self._zoom_pos = 0x555
        self._codes = (50, 50, 50)
        self._last_u: float | None = None
        self._last_manual = float("-inf")
        self.polling_paused = False  # калибровка: опросы зума и положения молчат, поток живёт
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="follower", daemon=True)
        self._zthread = threading.Thread(target=self._poll_zoom, name="follower-zoom", daemon=True)
        self._thread.start()
        self._zthread.start()

    # --- управление ----------------------------------------------------

    def enable(self) -> bool:
        if self.polling_paused:  # идёт калибровка: слежение камеру не двигает
            return False
        r = self.analyzer.latest() if self.analyzer else None
        if r is None or r.target_id is None:
            return False
        try:  # сразу узнать зум: от него зависит таблица скоростей
            z = self.query_zoom()
            if z is not None:
                self._zoom_pos = z
        except Exception as e:
            log.debug("Запрос зума при включении не удался: %s", e)
        with self._lock:
            latency = self.video_latency()
            self.controller.video_latency = latency
            self.controller.reset()
            self._codes = (50, 50, 50)
            self._last_u = None
            self.enabled = True
            self.last_reason = None
        log.info("Слежение включено (цель №%s)", r.target_id)
        # задержка видео — для повтора: регулятор вычитает её из момента кадра
        safe_event(self.recorder, "follow", on=True, reason=None, target_id=r.target_id, video_latency=latency)
        self._notify()
        return True

    def disable(self, reason: str) -> None:
        with self._lock:
            if not self.enabled:
                return
            self.enabled = False
            self.last_reason = reason
            self.output = None
        # стопы как ручные команды: уходят раньше следующих ручных (вызов пресета не будет прерван),
        # а ручная команда в те же ячейки «pt»/«zoom» их просто заменит
        self.scheduler.submit("pt", P.STOP_PT, Priority.MANUAL, only_if_changed=False)
        self.scheduler.submit("zoom", P.STOP_ZOOM, Priority.MANUAL, only_if_changed=False)
        log.info("Слежение выключено: %s", reason)
        safe_event(self.recorder, "follow", on=False, reason=reason)
        self._notify()

    def set_composition(self, c: Composition) -> None:
        with self._lock:
            self.controller.set_composition(c)
        self._record_settings()

    def set_effort(self, e: EffortProfile) -> None:
        with self._lock:
            self.controller.set_effort(e)
        self._record_settings()

    def _record_settings(self) -> None:
        with self._lock:
            comp, effort = self.controller.composition, self.controller.effort
        try:
            safe_event(self.recorder, "follow", composition=comp.key, effort=dataclasses.asdict(effort))
        except Exception as e:
            safe_event(self.recorder, "note", error=f"настройки слежения не записаны: {e}")

    @property
    def codes(self) -> tuple[int, int, int]:
        """Последние коды скоростей pan/tilt/zoom от слежения (50 — стоп)."""
        return self._codes

    def note_manual(self) -> None:
        """Сессия сообщает о ручной команде: опрос камеры ненадолго уступает оператору."""
        self._last_manual = time.monotonic()

    def pause_polling(self) -> None:
        """На время калибровки: слежение выключено, камеру не опрашиваем (объект и потоки остаются).

        Флаг — до выключения: вызов идёт из рабочего потока калибровки, и enable() из Qt между
        выключением и флагом снова включил бы слежение.
        """
        self.polling_paused = True
        self.disable("калибровка")

    def resume_polling(self) -> None:
        self.polling_paused = False

    def stop(self) -> None:
        self.disable("остановка камеры")
        self._stop.set()

    def _notify(self) -> None:
        cb = self.on_change
        if cb:
            try:
                cb(self.enabled, self.last_reason)
            except Exception:
                log.exception("on_change упал")

    # --- потоки ---------------------------------------------------------

    def _poll_zoom(self) -> None:
        # при слежении — каждые 0,5 с; без слежения, но с записью — раз в 2 с и не сразу после ручной команды
        last_idle_poll = float("-inf")
        while not self._stop.wait(ZOOM_POLL_S):
            try:
                recording = bool(self.recorder.active)
            except Exception:
                recording = False
            if self.polling_paused:
                continue
            if not self.enabled:
                now = time.monotonic()
                if (not recording or now - last_idle_poll < IDLE_POSITION_POLL_S
                        or now - self._last_manual < MANUAL_QUIET_S):
                    continue
                last_idle_poll = now
            try:
                z = self.query_zoom()
            except Exception as e:
                log.debug("Запрос зума не удался: %s", e)
                z = None
            if z is not None:
                self._zoom_pos = z
            if not self.enabled and time.monotonic() - self._last_manual < MANUAL_QUIET_S:
                continue  # пока шёл запрос зума, оператор взялся за камеру — положение не спрашиваем
            if recording and self.query_pan_tilt is not None:
                self._record_position(z)

    def _record_position(self, zoom: int | None) -> None:
        try:
            pt = self.query_pan_tilt()
        except Exception as e:
            log.debug("Запрос положения не удался: %s", e)
            pt = None
        if pt is not None:
            safe_event(self.recorder, "position", pan=pt[0], tilt=pt[1], zoom=zoom)

    def _run(self) -> None:
        period = 1.0 / LOOP_HZ
        while not self._stop.wait(period):
            if not self.enabled:
                continue
            try:
                self._tick()
            except Exception:
                log.exception("Ошибка в цикле слежения")
                self.disable("внутренняя ошибка")

    def _record_tick(self, now, r, measure, zoom, out, codes, comp: Composition, effort: EffortProfile) -> None:
        """Такт регулятора в запись: всё, что нужно, чтобы повторить его без камеры.

        Ошибка здесь не должна дойти до цикла слежения (он бы выключил слежение).
        """
        try:
            if not self.recorder.active:
                return
            m = None
            if measure is not None:
                m = {"nose": [float(v) for v in measure.nose],
                     "u": None if measure.u is None else float(measure.u), "confident": bool(measure.confident)}
            safe_event(
                self.recorder, "control", t=now, t_capture=r.t_capture, measure=m, zoom_pos=zoom,
                aspect=r.frame_size[0] / max(1, r.frame_size[1]),
                out={"pan": out.pan, "tilt": out.tilt, "zoom": out.zoom, "state": out.state,
                     "error": list(out.error), "size_error": out.size_error},
                codes=list(codes), composition=comp.key, effort=dataclasses.asdict(effort),
            )
        except Exception as e:
            safe_event(self.recorder, "note", error=f"такт регулятора не записан: {e}")

    def _tick(self) -> None:
        if self.polling_paused:
            return
        now = time.monotonic()
        r = self.analyzer.latest()
        stale_after = STALE_S + self.controller.video_latency  # кадры H.264 приходят с опозданием сами по себе
        if r is None or now - r.t_capture > stale_after or not self.analyzer.is_running():
            self.disable("анализ остановился")
            return
        measure = None
        if r.target_state == "tracking":
            tgt = next((t for t in r.tracks if t.id == r.target_id), None)
            if tgt is not None:
                measure = measure_body(tgt.keypoints, tgt.box, r.frame_size, self._last_u)
                if measure is not None and measure.u is not None:
                    self._last_u = measure.u
        zoom = self._zoom_pos
        with self._lock:
            if not self.enabled:
                return
            out = self.controller.step(
                FramingInput(now=now, t_capture=r.t_capture, measure=measure, zoom_pos=zoom,
                             aspect=r.frame_size[0] / max(1, r.frame_size[1]))
            )
            self.output = out
            diverged = out.state == "diverged"
            self._codes = output_codes(self.calib, out, zoom, self._codes)
            pan, tilt, zc = self._codes
            # отправка под замком: disable() не сможет вклиниться между проверкой enabled и submit
            self.scheduler.submit("pt", P.pan_tilt_speed(pan, tilt), Priority.TRACKING)
            self.scheduler.submit("zoom", P.zoom_speed(zc), Priority.TRACKING_ZOOM)
            comp, effort = self.controller.composition, self.controller.effort  # только ссылки: работа записи — вне замка
        self._record_tick(now, r, measure, zoom, out, (pan, tilt, zc), comp, effort)
        if diverged:
            self.disable("камера раскачивалась — она быстрее, чем ожидалось. Сервис → Калибровка камеры")
