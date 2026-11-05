"""Заряд геймпада: оценка времени до разряда. Чистые функции, без Qt и SDL."""

from __future__ import annotations


class BatteryEstimator:
    """Оценка времени до разряда по скорости падения уровня. Время приходит аргументом."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._last: int | None = None
        self._drops: list[tuple[float, int]] = []  # моменты, когда уровень стал ниже прежнего
        self._charging = False

    def add(self, t: float, percent: int | None, charging: bool | None) -> None:
        if percent is None:
            self.reset()
            return
        if charging:
            self.reset()
            self._charging = True
            self._last = percent
            return
        if self._charging or (self._last is not None and percent > self._last):
            self.reset()  # после зарядки или скачка вверх прежние точки врут
        if self._last is not None and percent < self._last:
            self._drops.append((t, percent))
        self._last = percent

    def remaining_s(self) -> float | None:
        if self._charging or len(self._drops) < 2 or self._last is None:
            return None
        (t0, p0), (t1, p1) = self._drops[0], self._drops[-1]
        if t1 <= t0 or p0 <= p1:
            return None
        return p1 / ((p0 - p1) / (t1 - t0))
