"""CameraSession — всё, что нужно для работы с одной камерой: команды, видео, состояние связи.

Интерфейс работает только через сессию, поэтому несколько камер в будущем —
это просто несколько сессий.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import platform
import re
import threading
import time
from collections import deque
from concurrent.futures import Future
from pathlib import Path
from typing import Callable

from ptztracker import __version__
from ptztracker.camera import he42
from ptztracker.camera import protocol as P
from ptztracker.camera.client import CameraClient
from ptztracker.camera.limit_watch import LimitWatch
from ptztracker.camera.models import IMAGE_FEATURES, ModelProfile, profile_for
from ptztracker.camera.scheduler import CommandScheduler, Priority
from ptztracker.camera.speed import velocity_to_code
from ptztracker.config import AppConfig, CameraConfig, get_password, record_dir
from ptztracker.control.stage_area import CameraView, StageArea, hfov_of_zoom_x, moved_view
from ptztracker.control.stage_area import flags as stage_flags
from ptztracker.recorder import NullRecorder, Recorder, safe_event
from ptztracker.video.frames import FrameSource
from ptztracker.video.rtsp import RtspSource, stream_url, stream_urls, with_credentials
from ptztracker.videorec.recorder import IDLE, RecState, VideoRecorder

log = logging.getLogger(__name__)

HEARTBEAT_S = 2.0
QUIET_PAUSE_S = 0.6
IMAGE_REFRESH_EVERY = 5  # опрос gain/WB — каждый 5-й heartbeat (~10 с): видеть изменения с пульта
ONE_SHOT_TTL_S = 2.0  # пресет/разовый AF, нажатые при обрыве связи, не выполняются позже
STOP_FLUSH_S = 1.5  # сколько ждать отправки «стопов» при остановке (если камера на связи)
SIM_START_TILT = -8.0
SIM_VIDEO_LATENCY_S = 0.15  # как MJPEG у живой камеры
# Без калибровки считаем камеру в 1,3 раза быстрее замеров (he42.PAN_DEG_S): запас устойчивости (раскачка —
# с ρ≈1,7, то есть камера может быть до ×2 быстрее таблицы). Было 1,5, потом 1,2 (см. DECISIONS)
UNCALIBRATED_SAFETY = 1.3
STEP_CACHE_S = 1.0  # столько живёт значение ириса/gain для шага без повторного опроса
PENDING_STEP_TTL_S = 2.0  # шаг, ждущий ответа камеры, устаревает
_BUSY_RE = re.compile(r"\ber2\b", re.IGNORECASE)  # ответ «занята»: планировщик повторит запрос
PTZ_HISTORY_S = 30.0  # история положения для служебной клетки «графики»
VIEW_MAX_AGE_S = 3.0  # область сцены: ответ #APC старше — вид камеры неизвестен, область не действует
VIEW_STILL_DEG = 0.05  # два ответа #APC подряд ближе — камера стоит (шаг шкалы 0,0083°)
VIEW_STILL_ZOOM_LN = 0.01  # то же для зума: ln отношения масштабов
VIEW_SETTLE_S = 0.5  # после ручной команды камера ещё докатывается: ответы за это время виду не годятся
PTZ_POLL_MIN_S = 0.4  # опрос положения не чаще: свежий ответ (любого источника) — не спрашиваем


@dataclasses.dataclass(frozen=True)
class PtzPosition:
    """Положение камеры по последним ответам #APC/#GZ; zoom_x — None, пока #GZ не пришёл."""

    t: float
    pan_deg: float
    tilt_deg: float
    zoom_x: float | None


@dataclasses.dataclass(frozen=True)
class ImageState:
    """Изображение по последним ответам камеры; None — ещё не известно."""

    gain: int | None = None
    wb: int | None = None
    color_temp: int | None = None
    iris: int | None = None
    iris_auto: bool | None = None
    shutter: int | None = None  # код OSH (protocol.SHUTTER_STEPS)
    synchro: int | None = None  # шаг OMS 001…0FF
    pedestal: int | None = None  # уровень чёрного −10…+10
    detail: int | None = None  # резкость 0 выкл, 1 низкая, 2 высокая
    preset_speed: int | None = None  # скорость переезда в пресет 1…30
    scene: int | None = None  # сцена: 1 Full Auto, 2–4 Manual 1–3 (protocol.SCENES) ⚠
    night: bool | None = None  # День / Ночь: True — ночной режим (чёрно-белая картинка)


@dataclasses.dataclass(frozen=True)
class CameraInfo:
    """Что выяснилось о камере при работе: окно сверяет это с настройками камеры и сохраняет."""

    model: str | None = None
    firmware: str | None = None
    unsupported: tuple[str, ...] = ()  # возможности, на которые камера ответила ER1 (camera.models.IMAGE_FEATURES)


_ER1 = re.compile(r"\ber1\b", re.IGNORECASE)
_ER3 = re.compile(r"\ber3\b", re.IGNORECASE)
# команды изображения, которые камера в Full Auto отклоняет ответом ER3 (баланс белого, gain, затвор, Synchro,
# уровень чёрного, резкость, цветовая температура, ирис и его автомат; замер на HE40 «TEST» 2026-11-06)
IMAGE_SET_PREFIXES = ("OAW:", "OGU:", "OSH:", "OMS:", "OTP:", "ODT:", "OSD:B1", "#AXI", "#D3")
REFUSED_SHOW_S = 6.0  # столько меню говорит «камера не приняла», после последнего отказа


def is_image_refusal(cmd: str, reply: str) -> bool:
    """Камера отклонила настройку изображения (ER3 — обычно режим Full Auto)."""
    return str(cmd).startswith(IMAGE_SET_PREFIXES) and bool(_ER3.search(str(reply)))
_ER_NO_RETRY = re.compile(r"\ber[13]\b", re.IGNORECASE)  # камера ответила, что QID не знает: больше не спрашиваем


def _feature_of(cmd: str) -> str | None:
    """Возможность, к которой относится команда или запрос (gain/WB/цветовая температура/питание)."""
    if cmd == P.Q_GAIN or cmd.startswith("OGU:"):
        return "gain"
    if cmd == P.Q_WB or cmd.startswith("OAW:"):
        return "wb"
    if cmd == P.Q_COLOR_TEMP or cmd.startswith("OSD:B1:"):
        return "color_temp"
    if cmd in (P.Q_POWER, P.POWER_ON, P.POWER_STANDBY):
        return "power"
    return None


class CameraSession:
    def __init__(
        self,
        cfg: CameraConfig,
        scene_path: str | None = None,
        analysis: bool = True,
        scene=None,
        sim_latency: float | None = None,
        app_cfg: AppConfig | None = None,  # настройки записи (record_*); None — по умолчанию
        password: str | None = None,  # явный пароль (команды --lab); None — из системного хранилища
        observe_only: bool = False,  # только смотреть и опрашивать (--lab record): ни движений, ни стопов
    ):
        self.observe_only = observe_only
        self.cfg = cfg
        self._password = password
        self.app_cfg = app_cfg or AppConfig()
        self.recorder: Recorder = NullRecorder()
        self.video: VideoRecorder | None = None  # запись видео в файл; сбой записи камеру не трогает
        self.scene_path = scene_path
        self._scene = scene  # для тестов: готовая сцена симулятора
        self._sim_latency = SIM_VIDEO_LATENCY_S if sim_latency is None else sim_latency
        self.analysis = analysis
        self.analyzer = None
        self.follower = None
        self.sim = None
        self._server = None
        self.client: CameraClient | None = None
        self.scheduler: CommandScheduler | None = None
        self.frames: FrameSource | None = None
        self.found_rtsp_url: str | None = None  # заполняется, когда подобран рабочий адрес видео
        self._hb_stop = threading.Event()
        self.limit_watch: LimitWatch | None = None
        self._quiet = False  # фоновые опросы остановлены на время калибровки
        self._hb_thread: threading.Thread | None = None
        self.on_manual: Callable[[], None] | None = None  # окно: ручное действие снимает разрешение агенту
        # питание и изображение — из ответов, которые планировщик и так получает; окно только читает
        self._power = "unknown"
        self._image = ImageState()
        self._last_manual_gain: int | None = None
        self._pending_steps: dict[str, tuple[int, float]] = {}  # что -> (шаг, момент запроса)
        self._step_lock = threading.RLock()
        self._img_t: dict[str, float] = {}
        # положение P/T/Z из ответов #APC/#GZ (любого источника: опрос окна, Follower, LimitWatch)
        self._ptz_lock = threading.Lock()
        self._ptz: PtzPosition | None = None
        self._ptz_hist: deque[PtzPosition] = deque()
        self._zoom_x: float | None = None
        self._ptz_poll_t = -1e9
        self._gz_reply_t = -1e9  # последний ответ #GZ
        self._manual_t = -1e9  # последняя ручная команда движения
        # для области сцены: (pan°, tilt°, момент ответа #APC, камера стоит) и (масштаб, момент ответа #GZ, зум стоит)
        self._view_apc: tuple | None = None
        self._view_gz: tuple | None = None
        self.stage_area = StageArea.from_json(cfg.stage_area)
        self._apc_t = -1e9  # последний ответ #APC: свежий — опрос не нужен (#GZ не в счёт)
        self._gz_t = -1e9  # последний ответ #GZ или запрос его (poll_zoom)
        # сведения о камере: стартуют с настроек, уточняются ответами камеры; настройки сессия не меняет —
        # окно читает camera_info() из потока Qt и сохраняет само
        self._info_lock = threading.Lock()
        self._model = cfg.model
        self._firmware = cfg.firmware
        self._unsupported = {f for f in cfg.unsupported if isinstance(f, str)}
        self._model_answered = False  # камера ответила на QID (или QID ей незнаком): больше не спрашиваем

    # --- жизненный цикл ------------------------------------------------

    def start(self) -> None:
        if self.cfg.kind == "sim":
            from ptztracker.sim.frame_source import SimFrameSource
            from ptztracker.sim.http_server import SimHttpServer
            from ptztracker.sim.ptz_model import SimPtz
            from ptztracker.sim.scene import SyntheticStage, VideoScene

            # Программа шлёт команды ровно раз в min_interval, но приходят они неровно (потоки, занятая машина). Живые
            # камеры за это не отказывают (838 команд поворота 2026-10-29/30 — 0 отказов), а симулятор с допуском 5 мс
            # отвергал 16 % команд (запись F9 2026-11-10) и портил слежение сильнее настоящей камеры
            self.sim = SimPtz(min_interval=self.cfg.min_interval, jitter_tolerance=0.5 * self.cfg.min_interval)
            self.sim.tilt_deg = SIM_START_TILT
            self._server = SimHttpServer(self.sim)
            self._server.start()
            self.client = CameraClient(self._server.url_host)
            scene = self._scene or (VideoScene(self.scene_path) if self.scene_path else SyntheticStage())
            self.frames = SimFrameSource(self.sim, scene, latency_s=self._sim_latency)
        else:
            password = self._password
            if password is None and self.cfg.username:
                password = get_password(self.cfg.id)
            self.client = CameraClient(self.cfg.ip, self.cfg.username, password)
            urls = stream_urls(self.cfg.ip, self.cfg.stream, self.cfg.username, password, self.cfg.rtsp_url)
            on_found = self._on_rtsp_found if self.cfg.stream == "auto" else None
            self.frames = RtspSource(urls, on_found=on_found)
        self.recorder = self._make_recorder()
        self.scheduler = CommandScheduler(
            self.client.send, min_interval=self.cfg.min_interval, on_sent=self._on_cmd_sent,
            queries_only=self.observe_only,
        )
        self.scheduler.add_sent_listener(self._on_reply)
        self.scheduler.probe_key = "heartbeat"  # пульс не голодает за повторами er2 (камера в ожидании)
        self.scheduler.start()
        self.limit_watch = LimitWatch(self._lw_query_pan_tilt, self._lw_query_zoom, lambda: None)
        self.frames.start()
        if self.analysis:
            self._start_analysis()
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(target=self._heartbeat, name="cam-heartbeat", daemon=True)
        self._hb_thread.start()
        self.refresh_image()
        self._ask_model()

    def _start_analysis(self) -> None:
        from ptztracker.vision.analyzer import Analyzer

        self.analyzer = Analyzer(self.frames, self._detector_factory(), recorder=self.recorder)
        self.analyzer.stage = self._stage_flags
        self.analyzer.start()
        self._start_follower()

    def set_analysis(self, on: bool) -> None:
        """Включить/выключить нейросеть на ходу (поток Qt). Выключение сначала гасит слежение — до остановки."""
        on = bool(on)
        if on == self.analysis:
            return
        self.analysis = on
        if self.frames is None or self._hb_stop.is_set():  # не запущена (start() учтёт флаг) или остановлена
            return
        if on:
            self._start_analysis()
            return
        f, a = self.follower, self.analyzer
        if f is not None:
            if f.enabled:
                f.disable("анализ отдан другой камере")
            f.stop()
        if a is not None:
            a.stop(timeout=0.2)
        self.analyzer = self.follower = None

    def _lw_query_pan_tilt(self):
        if self._quiet or not self.scheduler:
            return None
        return P.parse_pan_tilt(self.scheduler.query(P.Q_PAN_TILT, timeout=1.0))

    def _lw_query_zoom(self):
        if self._quiet or not self.scheduler:
            return None
        return P.parse_zoom(self.scheduler.query(P.Q_ZOOM, timeout=1.0))

    def stop(self) -> None:
        """Остановить камеру и освободить ресурсы, не подвешивая окно.

        «Стопы» ждём, только если камера на связи; потоки, застрявшие в сетевых
        таймаутах, дорабатывают в фоне (они daemon и сами завершатся).
        """
        self._hb_stop.set()
        if self.video:
            self._guarded("запись видео", self.video.stop)
        if self.limit_watch:
            self._guarded("упоры", self.limit_watch.stop)
            self.limit_watch = None
        if self.follower:
            self._guarded("слежение", self.follower.stop)
        if self.analyzer:
            self._guarded("анализ", self.analyzer.stop, timeout=0.2)  # поток daemon: если он в середине распознавания — доработает сам
        if self.scheduler:
            if not self.observe_only:  # наблюдатель камерой не управляет — и не останавливает её
                self.scheduler.emergency_stop()
                if self.scheduler.online:
                    self.scheduler.wait_emergency(STOP_FLUSH_S)
            self.scheduler.stop(timeout=0.2)
        if self.frames:
            self._guarded("видео", self.frames.stop, timeout=0.0)
        if self._server:
            self._guarded("сервер симулятора", self._server.stop)
        rec, self.recorder = self.recorder, NullRecorder()
        # close() дописывает постоянную запись на диск — в фоне, чтобы окно не ждало
        threading.Thread(target=rec.close, name="blackbox-close", daemon=True).start()

    def wait_video_stopped(self, timeout: float) -> bool:
        """После stop(), перед выходом из программы: дождаться потока приёма видео (stop() его не ждёт, чтобы окно не
        висло). True — поток закончился или его нет."""
        wait = getattr(self.frames, "wait", None)
        try:
            return bool(wait(timeout)) if wait else True
        except Exception:  # noqa: BLE001 — на выходе не падаем
            log.debug("Ожидание потока видео прервалось", exc_info=True)
            return True

    def start_video(self, folder: Path, mode: str, split_bytes: int | None) -> None:
        """Начать запись видео в файл. Только читает камеру: команд не шлёт, слежение и флажки не трогает."""
        if self.video and self.video.is_running:
            return
        source = ({"frames": self.frames} if self.sim is not None
                  else {"url": self._video_url, "shared": self._video_preview})
        self.video = VideoRecorder(self.cfg.name, folder, mode, split_bytes=split_bytes, **source)
        self.video.start()

    def stop_video(self) -> None:
        if self.video:
            self.video.stop()

    def video_state(self) -> RecState:
        return self.video.snapshot() if self.video else IDLE

    def video_note(self) -> str | None:
        return self.video.take_note() if self.video else None

    def _video_preview(self) -> RtspSource | None:
        """Просмотр, если он уже принимает H.264 камеры по RTSP: второму клиенту камера такой поток не отдаёт
        (AW-HE40, клуб, 2026-11-10), и запись берёт пакеты у него. Иначе None — запись подключается сама."""
        f = self.frames
        return f if isinstance(f, RtspSource) and f.shares_h264 else None

    def _video_url(self) -> str:
        """Вызывается из потока записи: хранилище паролей может отвечать долго."""
        if self.cfg.rtsp_url and self.cfg.rtsp_url.startswith("rtsp://"):
            url = self.cfg.rtsp_url
        else:
            url = stream_url(self.cfg.ip, "h264_1")
        password = self._password
        if password is None and self.cfg.username:
            password = get_password(self.cfg.id)
        return with_credentials(url, self.cfg.username, password)

    @staticmethod
    def _guarded(what: str, step: Callable[..., object], **kw) -> None:
        """Шаг остановки: сбой не отменяет следующие шаги и стоп камеры."""
        try:
            step(**kw)
        except Exception:
            log.exception("Остановка камеры: сбой (%s)", what)

    def quiet_for_calibration(self) -> None:
        """Выключить слежение и приостановить фоновые опросы (пульс, положение камеры) на время калибровки.

        Иначе они занимают окно команд мастера и камера отвечает «занята». Следящий объект остаётся тот же.
        Ждёт 0,6 с, пока уйдут уже отправленные команды, — вызывать из рабочего потока, не из Qt.
        """
        self._quiet = True
        if self.follower:
            self.follower.pause_polling()
        time.sleep(QUIET_PAUSE_S)

    def resume_after_calibration(self) -> None:
        """Вернуть пульс и опросы после quiet_for_calibration."""
        self._quiet = False
        if self.follower:
            self.follower.resume_polling()

    # --- запись (чёрный ящик) -------------------------------------------

    def save_moment(self, reason: str = "F9") -> Future[Path]:
        """Сохранить последние минуты записи в zip (в фоне). Без записи — Future с ошибкой."""
        try:
            meta = getattr(self.recorder, "meta", None)
            if isinstance(meta, dict):  # то, что выясняется уже после старта
                meta.update(self._live_meta())
            return self.recorder.save_snapshot(reason)
        except Exception as e:
            log.warning("Запись не сохранена: %s", e)
            fut: Future[Path] = Future()
            fut.set_exception(e)
            return fut

    def recording_meta(self) -> dict:
        """Описание сеанса для meta.json — только простые типы JSON, без пароля."""
        cam = dataclasses.asdict(self.cfg)
        cam.pop("calibration", None)  # действующая калибровка — отдельным полем
        return {
            "app": "PTZ Tracker",
            "version": __version__,
            "os": platform.platform(),
            "python": platform.python_version(),
            "camera": cam,
            "calibration": self.calibration().to_json(),
            "calibrated": bool(self.cfg.calibration),
            "record": {"buffer_s": int(self.app_cfg.record_buffer_s), "to_disk": bool(self.app_cfg.record_to_disk)},
            **self._live_meta(),
        }

    def _live_meta(self) -> dict:
        r = self.analyzer.latest() if self.analyzer else None
        return {
            "stream_kind": self.stream_kind(),
            "stage_area": None if self.stage_area is None else self.stage_area.to_json(),
            "camera_model": self.model,
            "camera_firmware": self.firmware,
            "camera_unsupported": list(self.camera_info().unsupported),
            "analysis_detector": r.detector if r else None,
            "analysis_device": r.device if r else None,
        }

    def _make_recorder(self) -> Recorder:
        a = self.app_cfg
        if not a.record_enabled:
            return NullRecorder()
        try:
            from ptztracker.recorder.writer import BlackBox

            return BlackBox(record_dir(a), self.recording_meta(), buffer_s=a.record_buffer_s,
                            to_disk=a.record_to_disk)
        except Exception as e:  # запись не должна мешать работе с камерой
            log.warning("Запись не запущена: %s", e)
            return NullRecorder()

    def _on_cmd_sent(self, info: dict) -> None:
        safe_event(self.recorder, "cmd", **info)

    def calibration(self):
        from ptztracker.control.camera_model import DEFAULT_TABLE_ID, Calibration, default_calibration

        calib = None
        if self.cfg.calibration:
            try:
                calib = Calibration.from_json(self.cfg.calibration)
            except Exception as e:  # повреждённая калибровка — работаем по средним значениям
                log.warning("Калибровка камеры %s не читается (%s) — беру средние значения", self.cfg.name, e)
        scale = self.cfg.speed_scale  # поправка, найденная слежением в прошлые разы
        if calib is None:
            calib = default_calibration(safety=UNCALIBRATED_SAFETY, model=self.model)
            if isinstance(scale, dict) and scale.get("table") != DEFAULT_TABLE_ID:
                log.info("Поправка pan/tilt камеры %s найдена для прежней таблицы — не беру (зум оставляю)", self.cfg.name)
                scale = {"zoom": scale["zoom"]} if "zoom" in scale else None  # зум от таблицы поворота не зависит
        calib.set_speed_scale(scale)
        return calib

    def stream_kind(self) -> str:
        if self.cfg.kind == "sim":
            return "sim"
        url = getattr(self.frames, "url", "") or ""
        if not url.startswith("http"):
            return "h264"
        return "mjpeg_2" if "1280x720" in url else "mjpeg"  # «mjpeg» — 640×360; под этим видом сохранены калибровки

    def _start_follower(self) -> None:
        from ptztracker.control.follower import Follower
        from ptztracker.control.zones import framing

        calib = self.calibration()

        def latency() -> float:
            return calib.latency_for(self.stream_kind())

        def query_zoom():
            return P.parse_zoom(self.scheduler.query(P.Q_ZOOM, timeout=1.0))

        def query_pan_tilt():
            return P.parse_pan_tilt(self.scheduler.query(P.Q_PAN_TILT, timeout=1.0))

        comp, effort = framing(self.cfg.composition, self.cfg.effort, self.cfg.zones)
        self.follower = Follower(
            self.analyzer,
            self.scheduler,
            calib,
            latency,
            comp,
            effort,
            query_zoom,
            recorder=self.recorder,
            query_pan_tilt=query_pan_tilt,
        )
        self.analyzer.motion = self.follower.picture_motion
        self.follower.stage_on = self.stage_area is not None and self.stage_area.on

    def _manual(self) -> None:
        """Ручное действие оператора: слежение и агент уступают немедленно, до отправки команды."""
        self._manual_t = time.monotonic()
        if self.on_manual:
            try:
                self.on_manual()
            except Exception:
                log.exception("on_manual упал")
        if self.follower and self.follower.enabled:
            self.follower.disable("ручное управление")

    def _detector_factory(self):
        """Симулятор с синтетической сценой — точная разметка; иначе — нейросеть."""
        if self.cfg.kind == "sim" and not self.scene_path:
            from ptztracker.vision.sim_detector import SimDetector

            return SimDetector

        def make():
            from ptztracker.vision.pose_detector import PoseDetector

            return PoseDetector()

        return make

    def _on_rtsp_found(self, url: str) -> None:
        self.found_rtsp_url = url

    def _heartbeat(self) -> None:
        # Лёгкий запрос «камера включена?» раз в 2 с — чтобы статус связи был актуален без движения.
        n = 0
        while True:
            if not self._quiet:
                self.scheduler.submit("heartbeat", P.Q_POWER, Priority.QUERY, only_if_changed=False)
                n += 1
                if n % IMAGE_REFRESH_EVERY == 0:
                    self.refresh_image()
                    if not self._model_answered and self.online and self._power not in ("standby", "switching"):
                        self._ask_model()  # при старте камера могла не ответить
            if self._hb_stop.wait(HEARTBEAT_S):
                return

    @property
    def online(self) -> bool:
        return bool(self.scheduler and self.scheduler.online)

    # --- модель и возможности камеры -------------------------------------

    @property
    def model(self) -> str | None:
        """Ответ камеры на QID («AW-HE40»); до ответа — из настроек, иначе None."""
        return self._model

    @property
    def firmware(self) -> str | None:
        """Версия прошивки. Запроса прошивки в протоколе Panasonic, который мы знаем, нет — берётся из настроек."""
        return self._firmware

    @property
    def profile(self) -> ModelProfile:
        return profile_for(self._model)

    def camera_info(self) -> CameraInfo:
        with self._info_lock:
            return CameraInfo(self._model, self._firmware, tuple(sorted(self._unsupported)))

    def refused_recently(self) -> bool:
        """Камера только что отклонила настройку изображения (ER3, режим Full Auto) — меню об этом скажет."""
        return time.monotonic() - getattr(self, "_refused_t", -1e9) < REFUSED_SHOW_S

    def supports(self, feature: str) -> bool:
        """False — камера отвечала ER1 на gain/WB/цветовую температуру/питание: кнопки скрыть или погасить."""
        return feature not in self._unsupported

    def _ask_model(self) -> None:
        """Спросить QID (очередь запросов, не ждём ответа: он разберётся в _on_reply)."""
        if self.scheduler:
            self.scheduler.submit("model", P.Q_MODEL, Priority.QUERY, only_if_changed=False)

    def _on_model_reply(self, ok: bool, reply: str) -> None:
        if ok:
            self._model_answered = True
            try:
                model = P.parse_model(reply)
            except ValueError:
                log.debug("Непонятный ответ на QID: %r", reply)
                return
            with self._info_lock:
                was = self._model
                if was is not None and was != model:
                    self._unsupported.clear()
                self._model = model
            if was is not None and was != model:  # журнал — вне блокировки
                log.info("Камера %s теперь %s (была %s) — забываю найденные возможности", self.cfg.name, model, was)
        elif _ER_NO_RETRY.search(reply):
            self._model_answered = True

    def _note_feature(self, cmd: str, ok: bool, reply: str) -> None:
        """ER1 на gain/WB/цветовую температуру/питание — камера этого не умеет. Успешный ответ снимает пометку."""
        feature = _feature_of(cmd)
        if feature is None or feature not in IMAGE_FEATURES:
            return
        added = False
        with self._info_lock:
            if ok:
                self._unsupported.discard(feature)
            elif _ER1.search(reply) and feature not in self._unsupported:
                self._unsupported.add(feature)
                added = True
        if added:  # журнал — вне блокировки
            log.info("Камера %s не знает %s (ER1) — запоминаю", self.cfg.name, cmd)

    # --- питание и изображение ------------------------------------------

    @property
    def power(self) -> str:
        """on | standby | switching | unknown (нет ответа или ответ непонятен)."""
        return self._power

    @property
    def image(self) -> ImageState:
        return self._image

    def refresh_image(self) -> None:
        if self._power in ("standby", "switching"):
            return  # камера в ожидании отвечает ER2 — опросим после включения
        now = time.monotonic()
        with self._step_lock:  # этот запрос уже стоит впереди опросов: его ждёт шаг оператора (_queue_step)
            ahead = {f"q_{what}" for what, (_, t) in self._pending_steps.items() if now - t <= PENDING_STEP_TTL_S}
        for key, cmd in (
            ("q_gain", P.Q_GAIN), ("q_wb", P.Q_WB), ("q_ctemp", P.Q_COLOR_TEMP), ("q_iris", P.Q_IRIS),
            ("q_shutter", P.Q_SHUTTER), ("q_synchro", P.Q_SYNCHRO), ("q_pedestal", P.Q_PEDESTAL),
            ("q_detail", P.Q_DETAIL), ("q_preset_speed", P.Q_PRESET_SPEED), ("q_scene", P.Q_SCENE),
            ("q_night", P.Q_NIGHT),
        ):
            if key not in ahead:
                self.scheduler.submit(key, cmd, Priority.QUERY, only_if_changed=False)

    # --- положение камеры ---------------------------------------------

    @property
    def ptz(self) -> PtzPosition | None:
        return self._ptz

    def ptz_history(self, seconds: float = PTZ_HISTORY_S) -> list[PtzPosition]:
        since = time.monotonic() - seconds
        with self._ptz_lock:
            return [p for p in self._ptz_hist if p.t >= since]

    def _add_ptz(self, p: PtzPosition) -> None:
        with self._ptz_lock:
            self._ptz = p
            self._ptz_hist.append(p)
            old = p.t - PTZ_HISTORY_S
            while self._ptz_hist and self._ptz_hist[0].t < old:
                self._ptz_hist.popleft()

    def set_stage_area(self, area: StageArea | None) -> None:
        """Область сцены этой камеры (None — не задана). Камере ничего не шлёт, слежение не трогает."""
        self.stage_area = area
        self.cfg.stage_area = None if area is None else area.to_json()
        if self.follower is not None:
            self.follower.stage_on = area is not None and area.on

    def _stage_flags(self, people, t: float, frame_size) -> list[bool] | None:
        area = self.stage_area
        if area is None or not area.on:
            return None
        return stage_flags(area, self.stage_view(t), people, frame_size)

    def stage_view(self, t: float) -> CameraView | None:
        """Куда смотрела камера в кадре, пришедшем в момент t: последний ответ #APC и зум, досчитанные моделью
        слежения. None — не знаем: ответа нет дольше VIEW_MAX_AGE_S или после него камеру двигали вручную."""
        apc, gz = self._view_apc, self._view_gz  # каждая пара (значение, момент, «стоит») читается целиком
        if apc is None or gz is None:
            return None
        (pan, tilt, apc_t, apc_still), (zoom_x, gz_t, gz_still) = apc, gz
        if abs(t - apc_t) > VIEW_MAX_AGE_S or abs(t - gz_t) > 2 * VIEW_MAX_AGE_S:
            return None  # ответ устарел — или кадр намного старше ответа (видео зависло)
        if min(apc_t, gz_t) < self._manual_t + VIEW_SETTLE_S:
            return None
        view = CameraView(pan, tilt, hfov_of_zoom_x(zoom_x))
        f = self.follower
        if f is None or not f.enabled:
            # без слежения модели движения нет: верим положению, только если камера стоит (два ответа подряд сошлись);
            # иначе это проезд пресета или чужой пульт — вид неизвестен
            return view if apc_still and gz_still else None
        try:
            # зум и положение камера сообщила в разные моменты: каждое досчитываем от своего ответа
            lat = f.video_latency()
            r = f.picture_motion(gz_t + lat, t)[0]
            _, sx, sy = f.picture_motion(apc_t + lat, t)
            return moved_view(moved_view(view, (r, 0.0, 0.0)), (1.0, sx, sy))
        except Exception as e:  # модель камеры не должна ронять анализ и окно
            log.debug("Вид камеры по модели не получен: %s", e)
            return None

    def poll_position(self) -> None:
        """Спросить #APC/#GZ (очередь QUERY), если свежего ответа нет; в ожидании камера ответит ER2 — не шлём."""
        if self.scheduler is None or self._quiet or self._power in ("standby", "switching"):
            return
        now = time.monotonic()
        if now - max(self._apc_t, self._ptz_poll_t) < PTZ_POLL_MIN_S:
            return
        self._ptz_poll_t = now
        self.scheduler.submit("q_apc", P.Q_PAN_TILT, Priority.QUERY, only_if_changed=False)
        self.scheduler.submit("q_gz", P.Q_ZOOM, Priority.QUERY, only_if_changed=False)

    @property
    def zoom_x(self) -> float | None:
        """Масштаб по последнему ответу #GZ (1 — широкий угол, he42.ZOOM_RATIO — теле); None — не знаем."""
        return self._zoom_x

    def poll_zoom(self, min_interval: float) -> None:
        """Спросить #GZ (очередь QUERY), если ответа не было min_interval с: «медленнее на крупном плане»."""
        if self.scheduler is None or self._quiet or self._power in ("standby", "switching"):
            return
        now = time.monotonic()
        if now - self._gz_t < min_interval:
            return
        self._gz_t = now
        self.scheduler.submit("q_gz", P.Q_ZOOM, Priority.QUERY, only_if_changed=False)

    def _on_ptz_reply(self, cmd: str, reply: str) -> None:
        if cmd == P.Q_ZOOM:
            frac = (P.parse_zoom(reply) - P.LENS_MIN) / (P.LENS_MAX - P.LENS_MIN)
            self._zoom_x = he42.ZOOM_RATIO ** min(1.0, max(0.0, frac))
            self._gz_t = self._gz_reply_t = time.monotonic()
            prev = self._view_gz
            still = (prev is not None and self._gz_t - prev[1] <= 2 * VIEW_MAX_AGE_S
                     and abs(math.log(self._zoom_x / prev[0])) < VIEW_STILL_ZOOM_LN)
            self._view_gz = (self._zoom_x, self._gz_t, still)
            p = self._ptz
            if p is not None:
                self._add_ptz(dataclasses.replace(p, t=time.monotonic(), zoom_x=self._zoom_x))
            return
        pan, tilt = P.parse_pan_tilt(reply)
        self._apc_t = time.monotonic()
        pan_deg = (pan - he42.APC_CENTER) * he42.APC_DEG_PER_UNIT
        tilt_deg = (tilt - he42.APC_CENTER) * he42.APC_DEG_PER_UNIT
        prev = self._view_apc
        still = (prev is not None and self._apc_t - prev[2] <= VIEW_MAX_AGE_S
                 and abs(pan_deg - prev[0]) < VIEW_STILL_DEG and abs(tilt_deg - prev[1]) < VIEW_STILL_DEG)
        self._view_apc = (pan_deg, tilt_deg, self._apc_t, still)
        self._add_ptz(PtzPosition(
            self._apc_t,
            (pan - he42.APC_CENTER) * he42.APC_DEG_PER_UNIT,
            (tilt - he42.APC_CENTER) * he42.APC_DEG_PER_UNIT,
            self._zoom_x,
        ))

    def _on_reply(self, info: dict) -> None:
        """Слушатель планировщика (его поток): разбор питания, изображения, положения. Не бросает исключений."""
        cmd, reply, ok = info.get("cmd"), str(info.get("reply") or ""), info.get("ok")
        try:
            if cmd == P.Q_MODEL:
                self._on_model_reply(bool(ok), reply)
                return
            self._note_feature(str(cmd), bool(ok), reply)
            if not ok and is_image_refusal(cmd, reply):
                self._refused_t = time.monotonic()
            if ok and cmd in (P.Q_PAN_TILT, P.Q_ZOOM):
                self._on_ptz_reply(cmd, reply)
                return
            if not ok and cmd in (P.Q_IRIS, P.Q_GAIN) and not _BUSY_RE.search(reply):
                # er2 «занята» планировщик повторяет — шаг ждёт ответа повтора; на другой ошибке он не придёт
                with self._step_lock:
                    self._pending_steps.pop("iris" if cmd == P.Q_IRIS else "gain", None)
            if cmd in (P.Q_POWER, P.POWER_ON, P.POWER_STANDBY):
                was = self._power
                now = P.parse_power(reply) if ok else ("unknown" if cmd == P.Q_POWER else was)
                if cmd == P.POWER_ON and now == "on" and was in ("standby", "switching"):
                    # #O1 отвечает p1 сразу, а камера ещё включается (p3 ~3,5 с). Только из ожидания: при «unknown»
                    # камера обычно уже включена, а «switching» не шлёт аварийные стопы
                    now = "switching"
                self._power = now
                if now in ("standby", "switching"):
                    with self._step_lock:
                        self._pending_steps.clear()
                    if was not in ("standby", "switching"):
                        self._enter_standby()
                if now == "on" and was in ("standby", "switching"):
                    self.refresh_image()
            elif ok and (cmd == P.Q_GAIN or str(cmd).startswith("OGU:")):
                g = P.parse_gain(reply)
                if g != P.GAIN_AUTO:
                    self._last_manual_gain = g
                self._upd(gain=g)
                self._apply_pending("gain")
            elif ok and (cmd == P.Q_WB or str(cmd).startswith("OAW:")):
                self._upd(wb=P.parse_wb(reply))
            elif ok and (cmd == P.Q_SHUTTER or str(cmd).startswith("OSH:")):
                self._upd(shutter=P.parse_shutter(reply))
            elif ok and (cmd == P.Q_SYNCHRO or str(cmd).startswith("OMS:")):
                self._upd(synchro=P.parse_synchro(reply))
            elif ok and (cmd == P.Q_PEDESTAL or str(cmd).startswith("OTP:")):
                self._upd(pedestal=P.parse_pedestal(reply))
            elif ok and (cmd == P.Q_DETAIL or str(cmd).startswith("ODT:")):
                self._upd(detail=P.parse_detail(reply))
            elif ok and (cmd == P.Q_SCENE or str(cmd).startswith("XSF:")):
                self._upd(scene=P.parse_scene(reply))
                if str(cmd).startswith("XSF:"):
                    self.refresh_image()  # другая сцена — другие ирис, gain, баланс белого
            elif ok and str(cmd).startswith(P.Q_NIGHT):  # и запрос #D6, и команда #D60/#D61 отвечают d6n
                self._upd(night=P.parse_night(reply))
            elif ok and str(cmd).startswith(P.Q_PRESET_SPEED):
                self._upd(preset_speed=P.parse_preset_speed(reply))
            elif ok and cmd == P.Q_IRIS:
                auto = P.parse_iris_auto(reply)
                if auto is None:
                    self._upd(iris=P.parse_iris(reply))
                else:
                    self._upd(iris=P.parse_iris(reply), iris_auto=auto)
                self._apply_pending("iris")
            elif ok and str(cmd).startswith("#AXI"):
                self._upd(iris=P.parse_iris(reply.replace("axi", "gi", 1)))
            elif ok and str(cmd).startswith("#D3"):
                self._upd(iris_auto=str(cmd)[3:] == "1")
            elif ok and (cmd == P.Q_COLOR_TEMP or str(cmd).startswith("OSD:B1:")):
                self._upd(color_temp=P.parse_color_temp(reply))
        except ValueError:
            log.debug("Непонятный ответ на %s: %r", cmd, reply)

    def _upd(self, **kw) -> None:
        now = time.monotonic()
        with self._step_lock:
            self._image = dataclasses.replace(self._image, **kw)
            for k in kw:
                self._img_t[k] = now

    def _fresh(self, key: str) -> bool:
        return time.monotonic() - self._img_t.get(key, -1e9) <= STEP_CACHE_S

    def _apply_pending(self, what: str) -> None:
        with self._step_lock:
            item = self._pending_steps.pop(what, None)
        if item and time.monotonic() - item[1] <= PENDING_STEP_TTL_S:
            (self._iris_apply if what == "iris" else self._gain_apply)(item[0])

    def _queue_step(self, what: str, d: int, key: str, cmd: str) -> bool:
        """Значение устарело или неизвестно: спросить камеру (без ожидания), шаг применится по ответу."""
        if self._power in ("standby", "switching"):
            return False
        now = time.monotonic()
        with self._step_lock:
            old = self._pending_steps.get(what)
            prev = old[0] if old and now - old[1] <= PENDING_STEP_TTL_S else 0
            self._pending_steps[what] = (max(-4, min(4, prev + d)), now)
        # Запрос, которого ждёт шаг оператора, — впереди фоновых опросов настроек картинки: их десяток по 0,13 с, и
        # в их хвосте шаг срабатывал через 1,5 с, а на медленной машине пропадал по сроку (CI macos-15, 2026-11-13).
        # В режиме наблюдения уходят только запросы — там приоритет прежний
        prio = Priority.QUERY if self.scheduler.queries_only else Priority.MANUAL
        self.scheduler.submit(key, cmd, prio, only_if_changed=False)
        return True

    def iris_step(self, d: int) -> bool:
        """Ирис на 1/16 хода шире (+1) или уже (−1). Слежение не трогает; не блокирует.
        True — команда отправлена или шаг ждёт ответа камеры."""
        img = self._image
        if img.iris_auto is False and img.iris is not None and self._fresh("iris"):
            return self._iris_apply(d)
        return self._queue_step("iris", d, "q_iris", P.Q_IRIS)

    def gain_step(self, d: int) -> bool:
        """Gain на следующий/предыдущий код (+1 ярче). Из AUTO — первое нажатие только снимает «Авто»:
        ручной на последнем виденном ручном значении, если его не видели — 0 dB (решение 2026-11-05)."""
        g = self._image.gain
        if g is not None and self._fresh("gain"):
            return self._gain_apply(d)
        return self._queue_step("gain", d, "q_gain", P.Q_GAIN)

    def _iris_apply(self, d: int) -> bool:
        pos = self._image.iris
        if pos is None or self._power in ("standby", "switching"):
            return False
        # без дедупликации: оператор мог включить авто на пульте камеры; флагу авто из #GI не верим
        self._submit("iris_mode", P.iris_auto(False), only_if_changed=False)
        self._upd(iris_auto=False)
        step = (P.LENS_MAX - P.LENS_MIN) // 16
        new = max(P.LENS_MIN, min(P.LENS_MAX, pos + (step if d > 0 else -step)))
        self._submit("iris", P.iris_abs(new))
        self._upd(iris=new)
        return True

    def _gain_apply(self, d: int) -> bool:
        g = self._image.gain
        if g is None or self._power in ("standby", "switching"):
            return False
        codes = P.GAIN_CODES
        if g == P.GAIN_AUTO:
            new = self._last_manual_gain if self._last_manual_gain in codes else codes[0]
        else:
            i = codes.index(g) if g in codes else min(range(len(codes)), key=lambda k: abs(codes[k] - g))
            new = codes[max(0, min(len(codes) - 1, i + (1 if d > 0 else -1)))]
        self.set_gain(new)
        self._last_manual_gain = new
        self._upd(gain=new)
        return True

    def _enter_standby(self) -> None:
        """Камера ушла в ожидание (нашей кнопкой или с пульта): как ручное действие — слежение и агент
        выключаются; очередь чистится, остаются пульс и питание (поток планировщика, вне его замка)."""
        self._manual()
        self.scheduler.drop_pending(keep={"heartbeat", "power"})

    def set_power(self, on: bool) -> None:
        if not on:
            self._manual()  # слежение и агент выключаются до #O0
        self._submit("power", P.POWER_ON if on else P.POWER_STANDBY, only_if_changed=False, ttl=ONE_SHOT_TTL_S)

    def set_gain(self, code: int) -> None:
        # всегда отправлять: значение могли поменять с пульта, и «то же, что в прошлый раз» уже не то
        self._submit("gain", P.gain(code), only_if_changed=False)

    def set_wb(self, mode: int) -> None:
        self._submit("wb", P.wb_mode(mode), only_if_changed=False)

    def set_color_temp(self, index: int) -> None:
        self._submit("ctemp", P.color_temp(index), only_if_changed=False)

    # затвор, Synchro, уровень чёрного, резкость (меню камеры) и скорость пресетов — камеру не двигают,
    # слежение не выключают; в Full Auto камера отвечает ER3, тогда остаётся её прежнее значение
    def set_shutter(self, code: int) -> None:
        self._submit("shutter", P.shutter(code), only_if_changed=False)

    def set_synchro(self, step: int) -> None:
        self._submit("synchro", P.synchro(step), only_if_changed=False)

    def set_pedestal(self, level: int) -> None:
        self._submit("pedestal", P.pedestal(level), only_if_changed=False)

    def set_detail(self, mode: int) -> None:
        self._submit("detail", P.detail(mode), only_if_changed=False)

    def set_scene(self, n: int) -> None:
        """Сцена камеры (Full Auto / Manual 1–3): выход из Full Auto — чтобы ирис, gain и баланс белого
        стали доступны."""
        self._submit("scene", P.scene(n), only_if_changed=False, ttl=ONE_SHOT_TTL_S)

    def set_night(self, on: bool) -> None:
        """День / Ночь. Камеру не двигает и слежение не выключает; картинка меняется резко (цвет ↔ ч/б)."""
        self._submit("night", P.night(on), only_if_changed=False, ttl=ONE_SHOT_TTL_S)

    def set_preset_speed(self, n: int) -> None:
        self._submit("preset_speed", P.preset_speed(n), only_if_changed=False)

    def set_osd(self, on: bool) -> None:
        """Меню Panasonic на камере: открыть или закрыть (картинку меню камера кладёт в своё видео)."""
        self._submit("osd", P.osd(on), only_if_changed=False, ttl=ONE_SHOT_TTL_S)

    def osd_key(self, key: str) -> None:
        """up | down | enter | back — кнопка пульта в меню Panasonic."""
        self._osd_n = getattr(self, "_osd_n", 0) + 1  # свой ключ на нажатие: очередь не сливает быстрые нажатия
        self._submit(f"osd_key_{self._osd_n}", P.OSD_KEYS[key], only_if_changed=False, ttl=ONE_SHOT_TTL_S)

    # --- ручное управление --------------------------------------------

    def _submit(self, key: str, cmd: str, only_if_changed: bool = True, ttl: float | None = None) -> None:
        if key != "power" and self._power in ("standby", "switching"):
            return  # камера в ожидании отвечает ER2, а er2 планировщик повторял бы без конца
        if self.follower:
            self.follower.note_manual()
        self.scheduler.submit(key, cmd, Priority.MANUAL, only_if_changed=only_if_changed, ttl=ttl)

    def set_pan_tilt_velocity(self, vx: float, vy: float) -> None:
        if vx or vy:
            self._manual()
        self._submit("pt", P.pan_tilt_speed(velocity_to_code(vx), velocity_to_code(vy)))

    def set_zoom_velocity(self, v: float) -> None:
        if v:
            self._manual()
        self._submit("zoom", P.zoom_speed(velocity_to_code(v)))

    def set_focus_auto(self, on: bool) -> None:
        self._submit("focus_mode", P.focus_auto(on))

    def push_focus(self) -> None:
        self._submit("focus_push", P.focus_push_auto(), only_if_changed=False, ttl=ONE_SHOT_TTL_S)

    def set_focus_velocity(self, v: float) -> None:
        if v:
            self._manual()
        self._submit("focus", P.focus_speed(velocity_to_code(v)))

    def set_iris_auto(self, on: bool) -> None:
        self._submit("iris_mode", P.iris_auto(on))

    def set_iris(self, pos01: float) -> None:
        pos01 = max(0.0, min(1.0, pos01))
        self._submit("iris", P.iris_abs(round(P.LENS_MIN + pos01 * (P.LENS_MAX - P.LENS_MIN))))

    def recall_preset(self, n: int) -> None:
        self._manual()
        self._submit("preset", P.preset_recall(n), only_if_changed=False, ttl=ONE_SHOT_TTL_S)

    def store_preset(self, n: int) -> None:
        self._manual()
        self._submit("preset_store", P.preset_store(n), only_if_changed=False, ttl=ONE_SHOT_TTL_S)

    def emergency_stop(self) -> None:
        # сбой подготовительного шага не отменяет сам стоп: каждый шаг — отдельно
        try:
            if self.follower:
                self.follower.note_manual()
        except Exception:
            log.exception("Аварийный стоп: не удалось сообщить слежению о ручном действии")
        try:
            if self.follower and self.follower.enabled:
                self.follower.disable("аварийный стоп")
        except Exception:
            log.exception("Аварийный стоп: не удалось выключить слежение")
        if self._power in ("standby", "switching"):
            # камера в ожидании не движется и стопы отвергает (ER2); повторы стопов вытеснили бы #O1
            self.scheduler.drop_pending(keep={"heartbeat", "power"})
            return
        self.scheduler.emergency_stop()
