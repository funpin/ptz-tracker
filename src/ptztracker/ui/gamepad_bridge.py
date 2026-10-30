"""Мост геймпада в Qt: намерения из потока опроса — очередным сигналом в поток Qt; полоса, вибрация, номер игрока.

Мост — «держатель» скорости геймпада: помнит последние vx, vy, vz. Стоп (reset_motion) обнуляет их и
запирает автомат раскладки, пока стики и курки не вернутся в центр. Движения, выданные потоком опроса до
стопа, но дошедшие до Qt после него, отбрасываются (номер поколения стопа).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal

from ptztracker.gamepad.device import GamepadThread, SdlBackend
from ptztracker.gamepad.feedback import (
    RUMBLE_FOLLOW_AUTO_OFF,
    RUMBLE_FOLLOW_ON,
    RUMBLE_LIMIT,
    PadStatus,
    is_auto_off,
    lightbar_color,
)
from ptztracker.gamepad.intents import PanTilt, Zoom
from ptztracker.gamepad.mapping import Mapper

log = logging.getLogger(__name__)


def pad_number_for(cam, cameras: list) -> int:
    """Номер камеры для огоньков игрока: pad_number, иначе цифры в конце имени («KZ_PTZ_2» → 2),
    иначе место в списке (с 1). Всегда 1…4."""
    n = getattr(cam, "pad_number", None)
    if not isinstance(n, int) or isinstance(n, bool):
        n = None
    if n is None:
        m = re.search(r"(\d+)\s*$", getattr(cam, "name", "") or "")
        n = int(m.group(1)) if m else None
    if n is None:
        ids = [c.id for c in cameras]
        n = ids.index(cam.id) + 1 if cam.id in ids else 1
    return max(1, min(4, n))


class _LockedMapper:
    """Mapper под замком: update идёт в потоке опроса, latch_stop/cancel_holds — в потоке Qt.

    gen растёт при каждом стопе; update_gen — поколение, при котором посчитан последний update
    (пишет и читает только поток опроса)."""

    def __init__(self, mapper: Mapper) -> None:
        self._m = mapper
        self._lock = threading.Lock()
        self.gen = 0
        self.update_gen = 0

    def update(self, pad, t):
        with self._lock:
            self.update_gen = self.gen
            return self._m.update(pad, t)

    def latch_stop(self) -> None:
        with self._lock:
            self.gen += 1
            self._m.latch_stop()

    def cancel_holds(self) -> None:
        with self._lock:
            self._m.cancel_holds()


class GamepadBridge(QObject):
    intents = Signal(list)  # в потоке Qt; движения, устаревшие после стопа, уже отброшены
    connectionChanged = Signal(object)  # info (dict) или None; в потоке Qt
    _raw = Signal(list, int)  # из потока опроса: намерения и поколение стопа
    _conn = Signal(object)  # из потока опроса
    _follow = Signal(bool, object)  # из потока слежения
    _limit = Signal()  # из потока LimitWatch

    def __init__(self, backend=None, menu_open: Callable[[], bool] | None = None, hz: float = 100.0,
                 parent=None) -> None:
        super().__init__(parent)
        self.connected = False
        self.info: dict | None = None
        self._vel = (0.0, 0.0, 0.0)
        self._led: tuple[int, int, int] | None = None
        self._player: int | None = None
        self._mapper = _LockedMapper(Mapper(menu_open or (lambda: False)))
        self._thread: GamepadThread | None = None
        self._raw.connect(self._on_raw, Qt.QueuedConnection)
        self._conn.connect(self._on_conn, Qt.QueuedConnection)
        self._follow.connect(self._on_follow, Qt.QueuedConnection)
        self._limit.connect(self._on_limit, Qt.QueuedConnection)
        if backend is None:
            backend = SdlBackend()
        try:
            ok = bool(backend.open())
        except Exception as e:
            log.warning("Геймпад недоступен: %s: %s", type(e).__name__, e)
            ok = False
        if not ok:
            return
        self._thread = GamepadThread(backend, self._mapper, self._emit_raw, self._conn.emit, hz=hz)
        self._thread.start()

    @property
    def available(self) -> bool:
        return self._thread is not None

    # --- поток опроса ---
    def _emit_raw(self, intents: list) -> None:
        self._raw.emit(list(intents), self._mapper.update_gen)

    # --- поток Qt ---
    def _on_raw(self, intents: list, gen: int) -> None:
        stale = gen != self._mapper.gen
        out = []
        for it in intents:
            if isinstance(it, (PanTilt, Zoom)):
                if stale:
                    continue
                vx, vy, vz = self._vel
                self._vel = (it.vx, it.vy, vz) if isinstance(it, PanTilt) else (vx, vy, it.v)
            out.append(it)
        if out:
            self.intents.emit(out)

    def _on_conn(self, info) -> None:
        self.connected = info is not None
        self.info = info
        if info is not None:
            log.info("Геймпад подключён: %s (%s), LED: %s", info.get("name"), info.get("type"), info.get("has_led"))
            if self._thread is not None and self._player is not None:
                self._thread.set_player(self._player)
        else:
            log.info("Геймпад отключён")
        self.connectionChanged.emit(info)

    def _on_follow(self, on: bool, reason) -> None:
        if on:
            self.rumble(RUMBLE_FOLLOW_ON)
        elif is_auto_off(reason):
            self.rumble(RUMBLE_FOLLOW_AUTO_OFF)

    def _on_limit(self) -> None:
        if any(self._vel):  # упор — пока давит геймпад
            self.rumble(RUMBLE_LIMIT)

    # --- из любого потока ---
    def notify_follow(self, on: bool, reason: str | None) -> None:
        """Follower.on_change (поток слежения или Qt)."""
        self._follow.emit(bool(on), reason)

    def notify_limit(self) -> None:
        """LimitWatch.on_limit (его поток)."""
        self._limit.emit()

    # --- вызовы из потока Qt ---
    def velocity(self) -> tuple[float, float, float]:
        return self._vel

    def zero_velocity(self) -> None:
        """Обнулить запомненную скорость (геймпад отключился) без запирания автомата."""
        self._vel = (0.0, 0.0, 0.0)

    def reset_motion(self) -> None:
        """Стоп: скорость — нули; чтобы камера снова поехала, стики и курки надо вернуть в центр."""
        self._vel = (0.0, 0.0, 0.0)
        self._mapper.latch_stop()

    def cancel_holds(self) -> None:
        self._mapper.cancel_holds()

    def set_status(self, s: PadStatus, t: float | None = None) -> None:
        """Цвет полосы — только при изменении."""
        if self._thread is None:
            return
        color = lightbar_color(s, time.monotonic() if t is None else t)
        if color != self._led:
            self._led = color
            self._thread.set_led(color)

    def set_player(self, n: int | None) -> None:
        self._player = n
        if self._thread is not None:
            self._thread.set_player(n)

    def rumble(self, pattern) -> None:
        if self._thread is not None:
            self._thread.rumble(pattern)

    def stop(self, timeout: float = 1.0) -> None:
        self._vel = (0.0, 0.0, 0.0)
        if self._thread is not None:
            self._thread.stop(timeout)
