"""Чёрный ящик: запись последних минут кадров и событий для разбора и повтора.

Запись — только наблюдатель: её вызовы не блокируют и не бросают исключений,
поэтому слежение и команды камере от неё не зависят.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Protocol, runtime_checkable

from ptztracker.video.frames import Frame

log = logging.getLogger(__name__)

LOG_EVERY_S = 10.0
_last_log = float("-inf")


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

    def recent_events(self, since: float, limit: int = 500) -> list[dict]:
        return []

    def close(self) -> None:
        pass


def safe_event(rec: Recorder, kind: str, **data) -> None:
    """recorder.event, который никогда не бросает: запись не должна мешать слежению."""
    try:
        rec.event(kind, **data)
    except Exception as e:
        _warn(f"событие {kind} не записано", e)


def safe_frame(rec: Recorder, fr: Frame) -> None:
    try:
        rec.frame(fr)
    except Exception as e:
        _warn("кадр не записан", e)


def _warn(what: str, e: Exception) -> None:
    global _last_log
    now = time.monotonic()
    if now - _last_log >= LOG_EVERY_S:  # не чаще раза в 10 с
        _last_log = now
        log.warning("Запись: %s: %s", what, e)
