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
from ptztracker.video.rtsp import RtspSource, rtsp_candidates

log = logging.getLogger(__name__)

HEARTBEAT_S = 2.0
SIM_START_TILT = -8.0  # чтобы в симуляторе докладчик сразу был в кадре


class CameraSession:
    def __init__(self, cfg: CameraConfig, scene_path: str | None = None):
        self.cfg = cfg
        self.scene_path = scene_path
        self.sim = None
        self._server = None
        self.client: CameraClient | None = None
        self.scheduler: CommandScheduler | None = None
        self.frames: FrameSource | None = None
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
            url = self.cfg.rtsp_url or rtsp_candidates(self.cfg.ip, self.cfg.username, password)[0]
            self.frames = RtspSource(url)
        self.scheduler = CommandScheduler(self.client.send, min_interval=self.cfg.min_interval)
        self.scheduler.start()
        self.frames.start()
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(target=self._heartbeat, name="cam-heartbeat", daemon=True)
        self._hb_thread.start()

    def stop(self) -> None:
        self._hb_stop.set()
        if self.scheduler:
            self.scheduler.emergency_stop()
            self.scheduler.wait_idle(0.5)
            self.scheduler.stop()
        if self.frames:
            self.frames.stop()
        if self._server:
            self._server.stop()
        if self.client:
            self.client.close()
        if self._hb_thread:
            self._hb_thread.join(1.0)

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

    def _submit(self, key: str, cmd: str, only_if_changed: bool = True) -> None:
        self.scheduler.submit(key, cmd, Priority.MANUAL, only_if_changed=only_if_changed)

    def set_pan_tilt_velocity(self, vx: float, vy: float) -> None:
        self._submit("pt", P.pan_tilt_speed(velocity_to_code(vx), velocity_to_code(vy)))

    def set_zoom_velocity(self, v: float) -> None:
        self._submit("zoom", P.zoom_speed(velocity_to_code(v)))

    def set_focus_auto(self, on: bool) -> None:
        self._submit("focus_mode", P.focus_auto(on))

    def push_focus(self) -> None:
        self._submit("focus_push", P.focus_push_auto(), only_if_changed=False)

    def set_focus_velocity(self, v: float) -> None:
        self._submit("focus", P.focus_speed(velocity_to_code(v)))

    def set_iris_auto(self, on: bool) -> None:
        self._submit("iris_mode", P.iris_auto(on))

    def set_iris(self, pos01: float) -> None:
        pos01 = max(0.0, min(1.0, pos01))
        self._submit("iris", P.iris_abs(round(P.LENS_MIN + pos01 * (P.LENS_MAX - P.LENS_MIN))))

    def recall_preset(self, n: int) -> None:
        self._submit("preset", P.preset_recall(n), only_if_changed=False)

    def store_preset(self, n: int) -> None:
        self._submit("preset_store", P.preset_store(n), only_if_changed=False)

    def emergency_stop(self) -> None:
        self.scheduler.emergency_stop()
