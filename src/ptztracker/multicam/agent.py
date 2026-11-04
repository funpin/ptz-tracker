"""Разрешения агенту для одной камеры: два флажка оператора, читаемые из любого потока."""

from __future__ import annotations

import threading
from typing import Callable


class AgentGate:
    """Снимки флажков «Разрешить управление агенту» и «Камера НЕ в эфире».

    Читаются потоками API без Qt; пишутся окном и `revoke()` (его можно звать из любого потока).
    Колбэки зовутся в вызвавшем потоке — окно само переносит их в поток Qt.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()  # проверка-и-запись флажков атомарна: revoke из потока сессии не теряется
        self.control_allowed = False
        self.not_on_air = False
        self.on_change: Callable[[], None] | None = None  # флажки изменились — перерисовать
        self.on_off_air_cleared: Callable[[], None] | None = None  # «НЕ в эфире» снят

    def set_allowed(self, on: bool) -> None:
        with self._lock:
            self.control_allowed = bool(on)
        self._changed()

    def set_off_air(self, on: bool) -> None:
        with self._lock:
            was = self.not_on_air
            self.not_on_air = bool(on)
        self._changed()
        if was and not on and self.on_off_air_cleared:
            self.on_off_air_cleared()

    def revoke(self) -> None:
        """Снять оба разрешения. Снимок меняется сразу."""
        with self._lock:
            was = self.not_on_air
            self.control_allowed = False
            self.not_on_air = False
        self._changed()
        if was and self.on_off_air_cleared:
            self.on_off_air_cleared()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()
