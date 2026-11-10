"""Слать ли хабу команду, когда в программе сменилась выбранная камера. Без сети и без Qt.

Первый отчёт после запуска окна только запоминается: при запуске хаб не трогаем (выход может быть в эфире).
Пропущенная по любой причине команда не откладывается.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ptztracker.videohub.client import HubSnapshot

_UNSET = object()


@dataclass(frozen=True)
class FollowResult:
    action: Literal["route", "skip"]
    reason: str  # route | first | same | off | no_output | no_input | offline | locked
    output: int = 0
    input: int = 0


class HubFollower:
    def __init__(self) -> None:
        self._last: object = _UNSET

    def reset(self) -> None:
        """Следующий отчёт — снова «первый» (например, хаб перенастроили)."""
        self._last = _UNSET

    def selected(self, camera_id: str | None, hub_input: int, output: int, enabled: bool,
                 snap: HubSnapshot) -> FollowResult:
        prev, self._last = self._last, camera_id
        if prev is _UNSET:
            return FollowResult("skip", "first")
        if prev == camera_id:
            return FollowResult("skip", "same")
        if not enabled:
            return FollowResult("skip", "off")
        if not output:
            return FollowResult("skip", "no_output")
        if not hub_input or camera_id is None:
            return FollowResult("skip", "no_input")
        if not snap.online or not snap.state.present:
            return FollowResult("skip", "offline")
        if snap.state.locks.get(output) == "L":
            return FollowResult("skip", "locked")
        return FollowResult("route", "route", output, hub_input)
