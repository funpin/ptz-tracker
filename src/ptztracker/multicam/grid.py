"""Сетка 2×2: до четырёх сессий камер. Без Qt; вызывается из потока окна."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from ptztracker.camera.session import CameraSession
from ptztracker.config import GRID_SLOTS, CameraConfig
from ptztracker.multicam.agent import AgentGate
from ptztracker.multicam.velocity import VelocityMixer

log = logging.getLogger(__name__)


def default_make_session(cfg: CameraConfig, analysis: bool) -> CameraSession:
    return CameraSession(cfg, analysis=analysis)


@dataclass
class Cell:
    cfg: CameraConfig
    session: CameraSession
    mixer: VelocityMixer
    gate: AgentGate


class CameraGrid:
    def __init__(self, make_session: Callable[[CameraConfig, bool], CameraSession] = default_make_session):
        self._make = make_session
        self.cells: list[Cell | None] = [None] * GRID_SLOTS
        self.selected: int | None = None
        self._closed: list = []  # сессии, закрытые close_all: их потоки приёма видео дожидаемся перед выходом

    def open(self, slot: int, cfg: CameraConfig, analysis: bool) -> Cell:
        other = self.slot_of(cfg.id)
        if other is not None and other != slot:
            raise ValueError(f"камера {cfg.id} уже открыта в клетке {other}")
        self.close(slot)
        session = self._make(cfg, analysis)
        gate = AgentGate()
        session.on_manual = gate.revoke
        try:
            session.start()
        except Exception:
            self._stop_one_session(session, cfg.id)
            raise
        cell = Cell(cfg, session, VelocityMixer(session), gate)
        self.cells[slot] = cell
        return cell

    def close(self, slot: int) -> None:
        cell = self.cells[slot]
        if cell is None:
            return
        self._disarm(cell)
        self.cells[slot] = None
        self._emergency_one(cell)  # стоп — в очередь сразу; остановка потоков — в фоне, окно не ждёт сеть
        threading.Thread(target=self._stop_one, args=(cell,), name="grid-close", daemon=True).start()

    def close_all(self, timeout: float = 1.5) -> None:
        """Остановить все сессии параллельно; ждать не дольше `timeout` в сумме."""
        cells = [c for c in self.cells if c is not None]
        self.cells = [None] * GRID_SLOTS
        self._closed += [c.session for c in cells]
        for c in cells:
            self._disarm(c)
        for c in cells:  # стопы — в очередь сразу, до медленной остановки потоков
            self._emergency_one(c)
        threads = []
        for c in cells:
            t = threading.Thread(target=self._stop_one, args=(c,), name="grid-stop", daemon=True)
            t.start()
            threads.append(t)
        deadline = time.monotonic() + timeout
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))

    def wait_video_stopped(self, timeout: float) -> bool:
        """Перед выходом из программы: дождаться потоков приёма видео закрытых камер (close_all их не ждёт). Поток,
        который ещё читает камеру, когда Python завершается, роняет процесс (отчёт macOS 2026-11-13)."""
        sessions, self._closed = self._closed, []
        deadline, ok = time.monotonic() + timeout, True
        for s in sessions:
            wait = getattr(s, "wait_video_stopped", None)
            if wait is not None:
                ok = bool(wait(max(0.01, deadline - time.monotonic()))) and ok
        return ok

    @staticmethod
    def _stop_one(cell: Cell) -> None:
        CameraGrid._stop_one_session(cell.session, cell.cfg.id)

    @staticmethod
    def _stop_one_session(session: CameraSession, camera_id: str) -> None:
        try:
            session.stop()
        except Exception:
            log.exception("Не удалось остановить камеру %s", camera_id)

    @staticmethod
    def _disarm(cell: Cell) -> None:
        """Снять разрешения и обнулить источники; сбой не отменяет стопы (ни этой камеры, ни других)."""
        try:
            cell.gate.revoke()
        except Exception:
            log.exception("Стоп: не снято разрешение агента камеры %s", cell.cfg.id)
        try:
            cell.mixer.clear_all()
        except Exception:
            log.exception("Стоп: не обнулены источники камеры %s", cell.cfg.id)

    def stop_cell(self, cell: Cell) -> None:
        """Стоп одной камеры (тачпад геймпада): разрешения, источники, затем аварийный стоп."""
        self._disarm(cell)
        self._emergency_one(cell)

    @staticmethod
    def _emergency_one(cell: Cell) -> None:
        try:
            cell.session.emergency_stop()
        except Exception:
            log.exception("Аварийный стоп камеры %s не отправлен", cell.cfg.id)

    def slot_of(self, camera_id: str) -> int | None:
        for i, c in enumerate(self.cells):
            if c is not None and c.cfg.id == camera_id:
                return i
        return None

    def emergency_stop_all(self) -> None:
        """Все камеры: сначала снять разрешения и обнулить источники, затем стопы; сбой одной не мешает другим."""
        cells = [c for c in self.cells if c is not None]
        for c in cells:
            self._disarm(c)
        for c in cells:
            self._emergency_one(c)

    def ids(self) -> list[str | None]:
        return [c.cfg.id if c else None for c in self.cells]
