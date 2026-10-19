"""CameraSession — всё, что нужно для работы с одной камерой: команды, видео, состояние связи.

Интерфейс работает только через сессию, поэтому несколько камер в будущем —
это просто несколько сессий.
"""

from __future__ import annotations

import logging
import threading

from ptztracker.camera import protocol as P
from ptztracker.camera.client import CameraClient
from ptztracker.camera.scheduler import CommandScheduler, Priority
from ptztracker.camera.speed import velocity_to_code
from ptztracker.config import CameraConfig, get_password
from ptztracker.video.frames import FrameSource
from ptztracker.video.rtsp import RtspSource, stream_urls

log = logging.getLogger(__name__)

HEARTBEAT_S = 2.0
ONE_SHOT_TTL_S = 2.0  # пресет/разовый AF, нажатые при обрыве связи, не выполняются позже
STOP_FLUSH_S = 1.5  # сколько ждать отправки «стопов» при остановке (если камера на связи)
SIM_START_TILT = -8.0  # чтобы в симуляторе докладчик сразу был в кадре


class CameraSession:
    def __init__(self, cfg: CameraConfig, scene_path: str | None = None, analysis: bool = True):
        self.cfg = cfg
        self.scene_path = scene_path
        self.analysis = analysis
        self.analyzer = None
        self.sim = None
        self._server = None
        self.client: CameraClient | None = None
        self.scheduler: CommandScheduler | None = None
        self.frames: FrameSource | None = None
        self.found_rtsp_url: str | None = None  # заполняется, когда подобран рабочий адрес видео
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None

    # --- жизненный цикл ------------------------------------------------

    def start(self) -> None:
        if self.cfg.kind == "sim":
            from ptztracker.sim.frame_source import SimFrameSource
            from ptztracker.sim.http_server import SimHttpServer
            from ptztracker.sim.ptz_model import SimPtz
            from ptztracker.sim.scene import SyntheticStage, VideoScene

            self.sim = SimPtz(min_interval=self.cfg.min_interval)
            self.sim.tilt_deg = SIM_START_TILT
            self._server = SimHttpServer(self.sim)
            self._server.start()
            self.client = CameraClient(self._server.url_host)
            scene = VideoScene(self.scene_path) if self.scene_path else SyntheticStage()
            self.frames = SimFrameSource(self.sim, scene)
        else:
            password = get_password(self.cfg.id) if self.cfg.username else None
            self.client = CameraClient(self.cfg.ip, self.cfg.username, password)
            urls = stream_urls(self.cfg.ip, self.cfg.stream, self.cfg.username, password, self.cfg.rtsp_url)
            on_found = self._on_rtsp_found if self.cfg.stream == "auto" else None
            self.frames = RtspSource(urls, on_found=on_found)
        self.scheduler = CommandScheduler(self.client.send, min_interval=self.cfg.min_interval)
        self.scheduler.start()
        self.frames.start()
        if self.analysis:
            from ptztracker.vision.analyzer import Analyzer

            self.analyzer = Analyzer(self.frames, self._detector_factory())
            self.analyzer.start()
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(target=self._heartbeat, name="cam-heartbeat", daemon=True)
        self._hb_thread.start()

    def stop(self) -> None:
        """Остановить камеру и освободить ресурсы, не подвешивая окно.

        «Стопы» ждём, только если камера на связи; потоки, застрявшие в сетевых
        таймаутах, дорабатывают в фоне (они daemon и сами завершатся).
        """
        self._hb_stop.set()
        if self.analyzer:
            self.analyzer.stop(timeout=0.2)  # поток daemon: если он в середине распознавания — доработает сам
        if self.scheduler:
            self.scheduler.emergency_stop()
            if self.scheduler.online:
                self.scheduler.wait_emergency(STOP_FLUSH_S)
            self.scheduler.stop(timeout=0.2)
        if self.frames:
            self.frames.stop(timeout=0.0)
        if self._server:
            self._server.stop()

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
        while True:
            self.scheduler.submit("heartbeat", P.Q_POWER, Priority.QUERY, only_if_changed=False)
            if self._hb_stop.wait(HEARTBEAT_S):
                return

    @property
    def online(self) -> bool:
        return bool(self.scheduler and self.scheduler.online)

    # --- ручное управление --------------------------------------------

    def _submit(self, key: str, cmd: str, only_if_changed: bool = True, ttl: float | None = None) -> None:
        self.scheduler.submit(key, cmd, Priority.MANUAL, only_if_changed=only_if_changed, ttl=ttl)

    def set_pan_tilt_velocity(self, vx: float, vy: float) -> None:
        self._submit("pt", P.pan_tilt_speed(velocity_to_code(vx), velocity_to_code(vy)))

    def set_zoom_velocity(self, v: float) -> None:
        self._submit("zoom", P.zoom_speed(velocity_to_code(v)))

    def set_focus_auto(self, on: bool) -> None:
        self._submit("focus_mode", P.focus_auto(on))

    def push_focus(self) -> None:
        self._submit("focus_push", P.focus_push_auto(), only_if_changed=False, ttl=ONE_SHOT_TTL_S)

    def set_focus_velocity(self, v: float) -> None:
        self._submit("focus", P.focus_speed(velocity_to_code(v)))

    def set_iris_auto(self, on: bool) -> None:
        self._submit("iris_mode", P.iris_auto(on))

    def set_iris(self, pos01: float) -> None:
        pos01 = max(0.0, min(1.0, pos01))
        self._submit("iris", P.iris_abs(round(P.LENS_MIN + pos01 * (P.LENS_MAX - P.LENS_MIN))))

    def recall_preset(self, n: int) -> None:
        self._submit("preset", P.preset_recall(n), only_if_changed=False, ttl=ONE_SHOT_TTL_S)

    def store_preset(self, n: int) -> None:
        self._submit("preset_store", P.preset_store(n), only_if_changed=False, ttl=ONE_SHOT_TTL_S)

    def emergency_stop(self) -> None:
        self.scheduler.emergency_stop()
