"""Слежение: поток, который по результатам анализа ведёт камеру (pan/tilt/zoom).

Работает внутри CameraSession. Любое ручное действие выключает слежение
(сессия вызывает disable), поэтому автоматика и оператор не борются за камеру.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import threading
import time
from typing import Callable

from ptztracker.camera import protocol as P
from ptztracker.camera.scheduler import CommandScheduler, Priority
from ptztracker.control.camera_model import Calibration
from ptztracker.control.composition import Composition, measure_body
from ptztracker.control.effort import EffortProfile
from ptztracker.control.framing import TILT_FROM_FH, FramingController, FramingInput, FramingOutput
from ptztracker.control.speed_adapt import SpeedAdapter
from ptztracker.recorder import NullRecorder, Recorder, safe_event

log = logging.getLogger(__name__)

LOOP_HZ = 25.0
STALE_S = 0.6  # результат анализа старше — анализ встал, камеру останавливаем
POLL_TICK_S = 0.25  # шаг цикла опроса: решает, что из #APC/#GZ пора спросить
APC_POLL_MOVING_S = 0.5  # при слежении, пока камера поворачивается (и 0,5 с после): подстройка скорости ждёт свежего #APC
APC_POLL_STILL_S = 1.0  # при слежении, камера стоит: сторожу и записи хватает раза в секунду
GZ_POLL_MOVING_S = 0.5  # зум едет (и 1 с после остановки): модель зума сверяется с #GZ
GZ_POLL_STILL_S = 2.0  # зум стоит: редкий опрос только чтобы модель зума не уходила от камеры
APC_MOVE_TAIL_S = 0.5
GZ_MOVE_TAIL_S = 1.0
IDLE_POSITION_POLL_S = 2.0  # слежение выкл., идёт запись: положение и зум реже — не мешать ручному управлению
MANUAL_QUIET_S = 2.0  # после ручной команды столько не опрашиваем (запрос занимает окно ~130 мс)
_ZOOM_CMD = re.compile(r"#Z(\d\d)")
_PT_CMD = re.compile(r"#PTS(\d\d)(\d\d)")


def sent_zoom_code(info: dict) -> int | None:
    """Код скорости зума из события отправки планировщика (on_sent), если ушла команда #Zxx."""
    if info.get("key") != "zoom" or not info.get("ok"):
        return None
    m = _ZOOM_CMD.fullmatch(str(info.get("cmd", "")))
    return int(m[1]) if m else None


def sent_pt_codes(info: dict) -> tuple[int, int] | None:
    """Коды pan/tilt из события отправки планировщика, если ушла команда #PTSxxyy."""
    if info.get("key") != "pt" or not info.get("ok"):
        return None
    m = _PT_CMD.fullmatch(str(info.get("cmd", "")))
    return (int(m[1]), int(m[2])) if m else None


def output_codes(
    calib: Calibration, out: FramingOutput, zoom_pos: int, current: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Скорости регулятора → коды pan/tilt/zoom камеры (с гистерезисом от текущих кодов).

    Общая для слежения и повтора записи: повтор должен давать те же коды, что ушли в камеру.
    Поправка скорости (calib.*_scale) учитывается: камера в k раз быстрее таблицы — код на скорость v/k.
    """
    p0, t0, z0 = current
    pan = calib.pan_code(out.pan, zoom_pos, current=p0)
    tilt = calib.pan_code(out.tilt * TILT_FROM_FH, zoom_pos, current=t0, axis="tilt")
    zc = calib.zoom_code(out.zoom, current=z0)
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
        # подстройка скорости: пишет поправку в calib (только этот поток и планировщик — под self._lock)
        self.adapter = SpeedAdapter(calib)
        self._adapt_apc_t: float | None = None
        self._adapt_gz_t: float | None = None
        self.enabled = False
        self.last_reason: str | None = None
        self.output: FramingOutput | None = None
        self.on_change: Callable[[bool, str | None], None] | None = None
        self._lock = threading.Lock()
        self._zoom_pos = 0x555
        self._zoom_pos_t: float | None = None  # когда пришёл ответ #GZ с _zoom_pos
        # последний ответ #APC (опрос идёт при записи): сторож раскачки сверяет по нему развороты камеры
        self._pan_tilt: tuple[int, int] | None = None
        self._pan_tilt_t: float | None = None
        # Номер включения слежения и с каким номером отправлена каждая команда зума слежения: команда,
        # поставленная до выключения, но ушедшая уже после нового включения, — не для нового регулятора
        self._gen = 0
        self._zoom_cmd_gen: dict[str, int] = {}
        self._codes = (50, 50, 50)
        self._last_u: float | None = None
        self._last_manual = float("-inf")
        self._pt_move_t = float("-inf")  # когда слежение в последний раз слало ненулевой поворот/наклон
        self._zoom_move_t = float("-inf")  # то же для зума
        self.polling_paused = False  # калибровка: опросы зума и положения молчат, поток живёт
        self._stop = threading.Event()
        scheduler.add_sent_listener(self._on_cmd_sent)
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
        self._zoom_pos_t = None  # старое положение не годится как опора модели зума
        try:  # сразу узнать зум: от него зависит таблица скоростей
            z = self.query_zoom()
            if z is not None:
                self._set_zoom_pos(z)
        except Exception as e:
            log.debug("Запрос зума при включении не удался: %s", e)
        with self._lock:
            latency = self.video_latency()
            self.controller.video_latency = latency
            self.controller.reset()
            self.adapter.reset()
            self._adapt_apc_t = self._adapt_gz_t = None
            self._pan_tilt = self._pan_tilt_t = None  # сторожу нового включения — только новые ответы #APC
            self._gen += 1
            self._zoom_cmd_gen.clear()
            self._codes = (50, 50, 50)
            self._pt_move_t = self._zoom_move_t = float("-inf")
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

    def picture_motion(self, t0: float, t1: float) -> tuple[float, float, float]:
        """Сдвиг картинки от команд слежения между двумя кадрами (для трекера людей; зовёт поток анализа).

        Слежение выключено — камера под ручным управлением, модель её не знает: сдвига нет.
        """
        with self._lock:
            if not self.enabled:
                return 1.0, 0.0, 0.0
            return self.controller.image_motion(t0, t1)

    def _set_zoom_pos(self, z: int) -> None:
        with self._lock:  # пара (положение, момент) читается тактом слежения целиком
            self._zoom_pos, self._zoom_pos_t = z, time.monotonic()

    def _on_cmd_sent(self, info: dict) -> None:
        """Планировщик отправил команду: команда зума — в модель зума регулятора (зум ждёт за поворотом);
        pan/tilt и зум — в подстройку скорости (что камера правда получила)."""
        code, pt = sent_zoom_code(info), sent_pt_codes(info)
        if code is None and pt is None:
            return
        t = float(info.get("t", time.monotonic()))  # начало отправки — по часам планировщика (monotonic)
        with self._lock:
            if not self.enabled:
                return
            if pt is not None:
                self.adapter.pt_sent(t, pt[0], pt[1], self._zoom_pos)
                return
            self.adapter.zoom_sent(t, code)  # и команда прежнего включения: камера её выполнила
            if info.get("priority") == Priority.TRACKING_ZOOM.name and self._zoom_cmd_gen.get(info["cmd"]) != self._gen:
                return  # команда прежнего включения слежения
            self.controller.zoom_sent(t, self.calib.zoom_rate(code))

    def _submit_tracking(self, pan: int, tilt: int, zc: int) -> None:
        """Коды слежения → в очередь (вызывать под self._lock)."""
        self.scheduler.submit("pt", P.pan_tilt_speed(pan, tilt), Priority.TRACKING)
        zcmd = P.zoom_speed(zc)
        self._zoom_cmd_gen[zcmd] = self._gen
        self.scheduler.submit("zoom", zcmd, Priority.TRACKING_ZOOM)

    def _notify(self) -> None:
        cb = self.on_change
        if cb:
            try:
                cb(self.enabled, self.last_reason)
            except Exception:
                log.exception("on_change упал")

    # --- потоки ---------------------------------------------------------

    def _poll_zoom(self) -> None:
        # при слежении: #APC раз в секунду (0,5 с, пока камера движется), #GZ только пока едет зум
        # и 1 с после (иначе раз в 2 с); без слежения, но с записью — раз в 2 с и не сразу после ручной команды
        last_idle_poll = float("-inf")
        last_apc = last_gz = float("-inf")
        while not self._stop.wait(POLL_TICK_S):
            try:
                recording = bool(self.recorder.active)
            except Exception:
                recording = False
            if self.polling_paused:
                continue
            now = time.monotonic()
            if not self.enabled:
                if (not recording or now - last_idle_poll < IDLE_POSITION_POLL_S
                        or now - self._last_manual < MANUAL_QUIET_S):
                    continue
                last_idle_poll = now
                do_gz = do_apc = True
            else:
                apc_every = APC_POLL_MOVING_S if now - self._pt_move_t < APC_MOVE_TAIL_S or self._codes[:2] != (50, 50) \
                    else APC_POLL_STILL_S
                gz_every = GZ_POLL_MOVING_S if now - self._zoom_move_t < GZ_MOVE_TAIL_S or self._codes[2] != 50 \
                    else GZ_POLL_STILL_S
                do_gz = now - last_gz >= gz_every - 1e-3
                do_apc = now - last_apc >= apc_every - 1e-3
                if not (do_gz or do_apc):
                    continue
            z = None
            if do_gz:
                last_gz = now
                try:
                    z = self.query_zoom()
                except Exception as e:
                    log.debug("Запрос зума не удался: %s", e)
                if z is not None:
                    self._set_zoom_pos(z)
                if not self.enabled and time.monotonic() - self._last_manual < MANUAL_QUIET_S:
                    continue  # пока шёл запрос зума, оператор взялся за камеру — положение не спрашиваем
            # при слежении положение нужно всегда (сторож раскачки, подстройка скорости), без него — для записи
            if do_apc and (recording or self.enabled) and self.query_pan_tilt is not None:
                last_apc = now
                self._poll_position(z if z is not None else self._zoom_pos, recording)

    def _poll_position(self, zoom: int | None, recording: bool) -> None:
        try:
            pt = self.query_pan_tilt()
        except Exception as e:
            log.debug("Запрос положения не удался: %s", e)
            pt = None
        if pt is not None:
            with self._lock:
                self._pan_tilt, self._pan_tilt_t = (int(pt[0]), int(pt[1])), time.monotonic()
            if recording:
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

    def _record_tick(self, now, r, measure, zoom, zoom_t, apc, apc_t, out, codes, comp: Composition,
                     effort: EffortProfile, scale: dict) -> None:
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
                self.recorder, "control", t=now, t_capture=r.t_capture, measure=m, zoom_pos=zoom, zoom_pos_t=zoom_t,
                pan_tilt=None if apc is None else list(apc), pan_tilt_t=apc_t,
                aspect=r.frame_size[0] / max(1, r.frame_size[1]),
                out={"pan": out.pan, "tilt": out.tilt, "zoom": out.zoom, "state": out.state,
                     "error": list(out.error), "size_error": out.size_error},
                codes=list(codes), composition=comp.key, effort=dataclasses.asdict(effort),
                speed_scale=scale,  # поправка, с которой посчитаны коды: повтор даёт те же
            )
        except Exception as e:
            safe_event(self.recorder, "note", error=f"такт регулятора не записан: {e}")

    def _adapt(self, zoom: int, zoom_t: float | None, apc, apc_t: float | None) -> None:
        """Новые ответы #GZ и #APC — в подстройку скорости (под self._lock). Сработал сторож — не менять."""
        frozen = self.controller.diverged
        if zoom_t is not None and zoom_t != self._adapt_gz_t:
            self._adapt_gz_t = zoom_t
            self.adapter.zoom_measured(zoom_t, zoom, frozen)
        if apc is not None and apc_t is not None and apc_t != self._adapt_apc_t:
            self._adapt_apc_t = apc_t
            self.adapter.pan_tilt_measured(apc_t, apc[0], apc[1], zoom, frozen)

    @property
    def speed_scale(self) -> dict[str, float]:
        """Текущая поправка скорости камеры (для сохранения в настройки из потока Qt)."""
        with self._lock:
            return self.calib.speed_scale

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
        with self._lock:
            if not self.enabled:
                return
            zoom, zoom_t = self._zoom_pos, self._zoom_pos_t
            apc, apc_t = self._pan_tilt, self._pan_tilt_t
            out = self.controller.step(
                FramingInput(now=now, t_capture=r.t_capture, measure=measure, zoom_pos=zoom,
                             aspect=r.frame_size[0] / max(1, r.frame_size[1]), zoom_pos_t=zoom_t,
                             pan_tilt=apc, pan_tilt_t=apc_t)
            )
            self.output = out
            diverged = out.state == "diverged"
            self._adapt(zoom, zoom_t, apc, apc_t)
            self._codes = output_codes(self.calib, out, zoom, self._codes)
            pan, tilt, zc = self._codes
            if pan != 50 or tilt != 50:
                self._pt_move_t = now
            if zc != 50:
                self._zoom_move_t = now
            # отправка под замком: disable() не сможет вклиниться между проверкой enabled и submit
            self._submit_tracking(pan, tilt, zc)
            comp, effort = self.controller.composition, self.controller.effort  # только ссылки: работа записи — вне замка
            scale = self.calib.speed_scale
        self._record_tick(now, r, measure, zoom, zoom_t, apc, apc_t, out, (pan, tilt, zc), comp, effort, scale)
        if diverged:
            self.disable("камера раскачивалась — она быстрее, чем ожидалось. Сервис → Калибровка камеры")
