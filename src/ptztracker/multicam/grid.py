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

    def open(self, slot: int, cfg: CameraConfig, analysis: bool) -> Cell:
        other = self.slot_of(cfg.id)
        if other is not None and other != slot:
            raise ValueError(f"камера {cfg.id} уже открыта в клетке {other}")
        self.close(slot)
        session = self._make(cfg, analysis)
        gate = AgentGate()
        session.on_manual = gate.revoke
        session.start()
        cell = Cell(cfg, session, VelocityMixer(session), gate)
        self.cells[slot] = cell
        return cell

    def close(self, slot: int) -> None:
        cell = self.cells[slot]
        if cell is None:
            return
        cell.gate.revoke()
        cell.mixer.clear_all()
        self.cells[slot] = None
        cell.session.stop()

    def close_all(self, timeout: float = 1.5) -> None:
        """Остановить все сессии параллельно; ждать не дольше `timeout` в сумме."""
        cells = [c for c in self.cells if c is not None]
        self.cells = [None] * GRID_SLOTS
        threads = []
        for c in cells:
            c.gate.revoke()
            c.mixer.clear_all()
            t = threading.Thread(target=self._stop_one, args=(c,), name="grid-stop", daemon=True)
            t.start()
            threads.append(t)
        deadline = time.monotonic() + timeout
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))

    @staticmethod
    def _stop_one(cell: Cell) -> None:
        try:
            cell.session.stop()
        except Exception:
            log.exception("Не удалось остановить камеру %s", cell.cfg.id)

    def slot_of(self, camera_id: str) -> int | None:
        for i, c in enumerate(self.cells):
            if c is not None and c.cfg.id == camera_id:
                return i
        return None

    def emergency_stop_all(self) -> None:
        for c in self.cells:
            if c is None:
                continue
            c.gate.revoke()
            c.mixer.clear_all()
            c.session.emergency_stop()

    def ids(self) -> list[str | None]:
        return [c.cfg.id if c else None for c in self.cells]
