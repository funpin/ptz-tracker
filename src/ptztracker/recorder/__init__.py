"""Чёрный ящик: запись последних минут кадров и событий для разбора и повтора.

Запись — только наблюдатель: её вызовы не блокируют и не бросают исключений,
поэтому слежение и команды камере от неё не зависят.
"""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from typing import Protocol, runtime_checkable

from ptztracker.video.frames import Frame


@runtime_checkable
class Recorder(Protocol):
    active: bool  # True — запись идёт (BlackBox), False — заглушка

    def frame(self, fr: Frame) -> None: ...

    def event(self, kind: str, **data) -> None: ...

    def save_snapshot(self, reason: str) -> Future[Path]: ...

    def close(self) -> None: ...


class NullRecorder:
    """Запись выключена: все вызовы ничего не делают."""

    active = False

    def frame(self, fr: Frame) -> None:
        pass

    def event(self, kind: str, **data) -> None:
        pass

    def save_snapshot(self, reason: str) -> Future[Path]:
        fut: Future[Path] = Future()
        fut.set_exception(RuntimeError("Запись выключена"))
        return fut

    def close(self) -> None:
        pass
