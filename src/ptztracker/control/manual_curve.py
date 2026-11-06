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


# Ступени L1/R1 геймпада: скорость поворота на упоре стика, °/с; каждая примерно в 1,6 раза быстрее предыдущей.
# Внутри ступени кривая shape раскладывается по °/с, а не по кодам камеры: таблица камеры крутая к краю
# (код 80 — 33 °/с, 99 — 90 °/с), и при раскладке по кодам последняя пятая часть хода давала больше половины скорости.
PAD_GEAR_DEG_S = (3, 5, 8, 13, 21, 34, 55, 90)


def gear_of(max_deg_s: float) -> int:
    """Номер ступени 1…len(PAD_GEAR_DEG_S), ближайшей к скорости на упоре max_deg_s."""
    return 1 + min(range(len(PAD_GEAR_DEG_S)), key=lambda i: abs(PAD_GEAR_DEG_S[i] - max_deg_s))


def deg_s_to_velocity(deg_s: float) -> float:
    """Нормированная скорость 0…1 (camera.speed.velocity_to_code), при которой камера даёт deg_s °/с
    (he42.PAN_DEG_S, линейно между точками); больше последней точки — 1."""
    if deg_s <= 0.0:
        return 0.0
    pts = [(50, 0.0), *sorted(he42.PAN_DEG_S.items())]
    code = float(pts[-1][0])
    for (c0, d0), (c1, d1) in zip(pts, pts[1:]):
        if deg_s <= d1:
            code = c0 + (c1 - c0) * (deg_s - d0) / (d1 - d0)
            break
    return min(1.0, (code - 51) / 48)


def pad_velocity(s: float, max_deg_s: float) -> float:
    """Стик после кривой shape (-1…1) → нормированная скорость: |s| — доля max_deg_s в °/с. Ненулевой наклон
    не меньше MIN_OUT (код 52): камера не стоит, пока стик отклонён."""
    a = min(1.0, abs(float(s)))
    if a == 0.0:
        return 0.0
    return math.copysign(max(MIN_OUT, deg_s_to_velocity(a * max_deg_s)), s)


TELE_SCALE_MIN = 0.15  # на теле не медленнее этой доли: иначе стик на полном наклоне почти не двигает камеру


def tele_scale(zoom_x: float | None) -> float:
    """«Медленнее на крупном плане»: множитель ручного поворота = угол обзора ÷ угол на широком (he42.hfov_deg),
    не меньше TELE_SCALE_MIN. Зум не известен — 1."""
    if zoom_x is None or not zoom_x > 1.0:
        return 1.0
    frac = min(1.0, math.log(zoom_x) / math.log(he42.ZOOM_RATIO))
    return max(TELE_SCALE_MIN, he42.hfov_deg(frac) / he42.HFOV_WIDE_DEG)
