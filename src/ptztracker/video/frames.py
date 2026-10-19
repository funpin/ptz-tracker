from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR uint8
    t_capture: float  # time.monotonic() момента получения кадра
    seq: int
    meta: dict | None = None  # служебные данные источника (симулятор кладёт сюда точную разметку)


@dataclass
class SourceStats:
    connected: bool
    fps: float
    width: int
    height: int
    error: str | None
    url: str


class FrameSource(Protocol):
    def start(self) -> None: ...

    def stop(self, timeout: float = 2.0) -> None: ...

    def latest(self) -> Frame | None: ...

    def stats(self) -> SourceStats: ...


class FpsMeter:
    """Скользящая оценка кадров в секунду (окно ~1 с)."""

    def __init__(self):
        self._times: list[float] = []

    def tick(self, t: float) -> None:
        self._times.append(t)
        cutoff = t - 1.0
        while self._times and self._times[0] < cutoff:
            self._times.pop(0)

    def fps(self, now: float) -> float:
        recent = [x for x in self._times if x >= now - 1.0]
        return float(len(recent))
