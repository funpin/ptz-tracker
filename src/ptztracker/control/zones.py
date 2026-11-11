"""Своя зона кадра: где держать лицо и где граница, из которой человека не выпускают.

Оператор тянет зону мышью прямо на видео (макеты `docs/design/mockups/drawer-and-frame.html`, `zone-boundary.html`).
Точка — где держать лицо; внутренняя рамка — покой (пока лицо в ней, камера стоит); скобки — граница: чем ближе к ней
человек и чем быстрее идёт, тем раньше и сильнее едет камера. Ширина и высота зоны задаются отдельно.
Хранится у камеры для каждой крупности: `CameraConfig.zones = {ключ композиции: [x, y, ширина, высота]}`;
запись прежних версий `[x, y, масштаб]` читается как одинаковые ширина и высота.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from ptztracker.control.composition import COMPOSITIONS, DEFAULT_COMPOSITION, Composition
from ptztracker.control.effort import EffortProfile

X_RANGE = (0.10, 0.90)  # точка лица не ближе 10 % к краю кадра
Y_RANGE = (0.10, 0.90)
SCALE_RANGE = (0.5, 1.8)  # во сколько раз зона уже или шире обычной (ширина и высота — каждая в этих пределах)
# Обычная зона — одна для всех характеров: «Мягко … Резко» отвечает за плавность, размер зоны задаёт оператор.
ZONE_SOFT = (0.22, 0.18)  # полуразмеры границы (скобки), доли ширины и высоты кадра
ZONE_DEAD = (0.05, 0.045)  # полуразмеры покоя (внутренняя рамка)
EDGE_MARGIN = 0.06  # граница не дальше края кадра минус столько: за краем человека уже не видно
DEAD_SHARE_MAX = 0.6  # покой — не больше этой доли границы (когда границу обрезал край кадра)
# Плашка зоны: человек успевает уйти от точки на v·(T + ZONE_NOTICE_S), пока камера его догонит. T — «позднее знание»
# регулятора (control.timing), ZONE_NOTICE_S — сколько ещё уходит, чтобы отличить первый шаг от переминания
# (стенд 2026-11-11, T 0,41 с: шаг 0,35 кадра/с с места — уход 0,25; 0,7 — 0,47).
ZONE_NOTICE_S = 0.27
CALM_STEP, FAST_STEP = 0.35, 0.7  # доли кадра/с: спокойный и быстрый шаг на «Поясе»


def _clip(v: float, r: tuple[float, float]) -> float:
    return min(r[1], max(r[0], v))


@dataclass(frozen=True)
class Zone:
    x: float  # где лицо, доли ширины кадра
    y: float  # доли высоты
    w: float = 1.0  # во сколько раз зона шире обычной
    h: float = 1.0  # во сколько раз выше

    @staticmethod
    def clamped(x: float, y: float, w: float = 1.0, h: float | None = None) -> Zone:
        return Zone(_clip(x, X_RANGE), _clip(y, Y_RANGE), _clip(w, SCALE_RANGE), _clip(w if h is None else h, SCALE_RANGE))

    def to_json(self) -> list[float]:
        return [round(self.x, 4), round(self.y, 4), round(self.w, 3), round(self.h, 3)]


def zone_of(zones, key: str) -> Zone | None:
    """Зона из настроек камеры; испорченная запись — как будто её нет. Три числа — запись прежних версий."""
    v = zones.get(key) if isinstance(zones, dict) else None
    if not isinstance(v, (list, tuple)) or len(v) not in (3, 4):
        return None
    try:
        if not all(isinstance(a, (int, float)) and not isinstance(a, bool) and math.isfinite(a) for a in v):
            return None
        return Zone.clamped(float(v[0]), float(v[1]), float(v[2]), float(v[-1]))
    except OverflowError:  # целое в сотни цифр из испорченного файла настроек
        return None


def _half(normal: float, size: float, point: float) -> float:
    """Полуразмер границы: обычный × размер оператора, но не дальше края кадра минус EDGE_MARGIN."""
    return max(min(normal * size, min(point, 1.0 - point) - EDGE_MARGIN), 0.5 * normal * SCALE_RANGE[0])


def framing(key: str, effort: float, zones) -> tuple[Composition, EffortProfile]:
    """Композиция и характер с зоной этой крупности: своя зона оператора или обычная."""
    comp = COMPOSITIONS.get(key, COMPOSITIONS[DEFAULT_COMPOSITION])
    z = zone_of(zones, comp.key)
    if z is not None:
        comp = dataclasses.replace(comp, anchor=(z.x, z.y))
    w, h = (z.w, z.h) if z is not None else (1.0, 1.0)
    soft_x, soft_y = _half(ZONE_SOFT[0], w, comp.anchor[0]), _half(ZONE_SOFT[1], h, comp.anchor[1])
    old = EffortProfile.at(effort)
    dead_x, dead_y = min(ZONE_DEAD[0] * w, DEAD_SHARE_MAX * soft_x), min(ZONE_DEAD[1] * h, DEAD_SHARE_MAX * soft_y)
    e = dataclasses.replace(
        old, soft_x=soft_x, soft_y=soft_y, dead_x=dead_x, dead_y=dead_y, zone_w=w, zone_h=h,
        # Сторож раскачки слабее стать не должен: его граница — не больше прежней границы этого характера, его
        # покой — не меньше прежнего (у «Резко» были 0,18 и 0,03, у «Мягко» — 0,25 и 0,07)
        watch_soft_x=min(soft_x, old.soft_x * w), watch_soft_y=min(soft_y, old.soft_y * h),
        watch_dead_x=max(dead_x, old.dead_x * w), watch_dead_y=max(dead_y, old.dead_y * h),
    )
    return comp, e


def zone_hint(zone_x: float, zone_y: float, t_late: float) -> str:
    """Плашка зоны: размер в процентах кадра и что эта зона значит для этой камеры и потока."""
    reach = t_late + ZONE_NOTICE_S  # за столько секунд человек уходит, пока камера его догонит
    if zone_x >= FAST_STEP * reach:
        what = "хватает и на быстрый шаг"
    elif zone_x >= CALM_STEP * reach:
        what = "хватает на спокойный шаг"
    else:
        what = "плотнее, чем успевает камера"
    return f"Зона ±{round(100 * zone_x)} % × ±{round(100 * zone_y)} % · {what}"
