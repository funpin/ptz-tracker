"""Гладкие функции движения: вес между порогами и кривая «отклонение → скорость» без ступеней."""

from __future__ import annotations

import math

SOFT_START = 0.5  # насколько мягко кривая начинается: доля мёртвой зоны
GROWTH = 0.6  # насколько сильнее, чем прямая пропорция, растёт скорость за мягкой зоной


def smoothstep(x: float, a: float, b: float) -> float:
    """0 ниже a, 1 выше b, между ними — плавная S-образная кривая (не ломается на краях)."""
    if b <= a:
        return 1.0 if x >= b else 0.0
    u = min(1.0, max(0.0, (x - a) / (b - a)))
    return u * u * (3 - 2 * u)


def speed_for_error(e: float, dead: float, soft: float, gain: float, v_cap: float) -> float:
    """Скорость по отклонению: у метки почти ноль, дальше растёт всё сильнее, у потолка мягко насыщается."""
    a, knee = abs(e), SOFT_START * dead
    v = gain * (math.hypot(a, knee) - knee) * (1 + GROWTH * (a / soft) ** 2)
    return math.copysign(v_cap * math.tanh(v / v_cap), e)
