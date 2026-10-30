"""Заряд DualSense: разбор сырого отчёта и оценка времени до разряда. Чистые функции, без Qt и SDL."""

from __future__ import annotations

REPORT_BT = 0x31   # Bluetooth: байт состояния по индексу 54
REPORT_USB = 0x01  # USB: индекс 53
_STATUS_INDEX = {REPORT_BT: 54, REPORT_USB: 53}


def parse_dualsense_battery(report: bytes) -> tuple[int, bool] | None:
    """(процент 0…100 шагом 10, заряжается) из отчёта DualSense; None — не тот отчёт или мусор.

    Младшая тетрада — уровень 0…10, старшая: 0 разряжается, 1 заряжается, 2 полный (100 %, не заряжается)."""
    if not report:
        return None
    idx = _STATUS_INDEX.get(report[0])
    if idx is None or len(report) <= idx:
        return None
    b = report[idx]
    level, state = b & 0x0F, b >> 4
    pct = min(100, level * 10)
    if state == 0:
        return pct, False
    if state == 1:
        return pct, True
    if state == 2:
        return 100, False
    return None


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
