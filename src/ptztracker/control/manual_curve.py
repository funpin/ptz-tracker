"""Кривая ручного управления «наклон стика → скорость»: ползунок «Плавно ↔ Резко».

Только ручной поворот и наклон (мышь, стрелки, геймпады); регулятор слежения её не видит.
Причина и числа — docs/DECISIONS.md, «Чувствительность ручного управления».
"""

from __future__ import annotations

import math

from ptztracker.camera import he42

MIN_OUT = 0.02  # camera.speed.velocity_to_code: меньше — код 50 (стоит); кривая не расширяет мёртвую зону
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
    """|v|^e со знаком v; ±1 остаётся ±1, ноль — ноль, не убывает. Наклон, который до кривой двигал камеру,
    двигает и после: не меньше самой малой скорости MIN_OUT."""
    a = min(1.0, abs(float(v)))
    if a == 0.0:
        return 0.0
    out = a ** exponent(sens)
    if a >= MIN_OUT:
        out = max(out, MIN_OUT)
    return math.copysign(out, v)


TELE_SCALE_MIN = 0.15  # на теле не медленнее этой доли: иначе стик на полном наклоне почти не двигает камеру


def tele_scale(zoom_x: float | None) -> float:
    """«Медленнее на крупном плане»: множитель ручного поворота = угол обзора ÷ угол на широком (he42.hfov_deg),
    не меньше TELE_SCALE_MIN. Зум не известен — 1."""
    if zoom_x is None or not zoom_x > 1.0:
        return 1.0
    frac = min(1.0, math.log(zoom_x) / math.log(he42.ZOOM_RATIO))
    return max(TELE_SCALE_MIN, he42.hfov_deg(frac) / he42.HFOV_WIDE_DEG)
