"""Перевод нормированной скорости (-1..1) в код скорости камеры 01–99 (50 — стоп)."""

import math


def velocity_to_code(v: float, dead: float = 0.02) -> int:
    if abs(v) < dead:
        return 50
    mag = 1 + round(48 * min(abs(v), 1.0))
    return 50 + int(math.copysign(mag, v))
