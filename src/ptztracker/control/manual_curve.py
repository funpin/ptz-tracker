"""Кривая ручного управления «наклон стика → скорость»: ползунок «Плавно ↔ Резко».

Только ручной поворот и наклон (мышь, стрелки, геймпады); регулятор слежения её не видит.
Причина и числа — docs/DECISIONS.md, «Чувствительность ручного управления».
"""

from __future__ import annotations

import math

DEFAULT_SENS = 50  # степень 2: мягче у центра, чем линейная кривая 1.0.1
SENS_MIN, SENS_MAX = 0, 100


def clamp_sens(sens) -> int:
    """0…100; мусор — по умолчанию."""
    try:
        return max(SENS_MIN, min(SENS_MAX, int(sens)))
    except (TypeError, ValueError):
        return DEFAULT_SENS


def exponent(sens: float) -> float:
    """Степень кривой: 100 («Резко») — 1, линейная как в 1.0.1; 0 («Плавно») — 3."""
    s = max(SENS_MIN, min(SENS_MAX, float(sens)))
    return 3.0 - 2.0 * s / SENS_MAX


def shape(v: float, sens: float) -> float:
    """|v|^e со знаком v; ±1 остаётся ±1, ноль — ноль, монотонна."""
    a = min(1.0, abs(float(v)))
    if a == 0.0:
        return 0.0
    return math.copysign(a ** exponent(sens), v)
