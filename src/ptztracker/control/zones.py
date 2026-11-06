"""Своя зона кадра: где держать лицо и насколько велики мёртвая и мягкая зоны.

Оператор тянет зону мышью прямо на видео (набросок `docs/design/mockups/drawer-and-frame.html`).
Хранится у камеры для каждой крупности: `CameraConfig.zones = {ключ композиции: [x, y, масштаб]}`.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from ptztracker.control.composition import COMPOSITIONS, DEFAULT_COMPOSITION, Composition
from ptztracker.control.effort import EffortProfile

X_RANGE = (0.10, 0.90)  # точка лица не ближе 10 % к краю кадра
Y_RANGE = (0.10, 0.90)
SCALE_RANGE = (0.5, 1.8)  # 1.8: самая широкая мягкая зона («Мягко», 0,25) — ещё в кадре
SOFT_MAX = 0.5  # полуширина мягкой зоны не больше половины кадра


def _clip(v: float, r: tuple[float, float]) -> float:
    return min(r[1], max(r[0], v))


@dataclass(frozen=True)
class Zone:
    x: float  # где лицо, доли ширины кадра
    y: float  # доли высоты
    scale: float = 1.0  # во сколько раз зоны больше обычных

    @staticmethod
    def clamped(x: float, y: float, scale: float = 1.0) -> Zone:
        return Zone(_clip(x, X_RANGE), _clip(y, Y_RANGE), _clip(scale, SCALE_RANGE))

    def to_json(self) -> list[float]:
        return [round(self.x, 4), round(self.y, 4), round(self.scale, 3)]


def zone_of(zones, key: str) -> Zone | None:
    """Зона из настроек камеры; испорченная запись — как будто её нет."""
    v = zones.get(key) if isinstance(zones, dict) else None
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    if not all(isinstance(a, (int, float)) and not isinstance(a, bool) and math.isfinite(a) for a in v):
        return None
    return Zone.clamped(float(v[0]), float(v[1]), float(v[2]))


def framing(key: str, effort: float, zones) -> tuple[Composition, EffortProfile]:
    """Композиция и характер с учётом своей зоны этой крупности."""
    comp = COMPOSITIONS.get(key, COMPOSITIONS[DEFAULT_COMPOSITION])
    e = EffortProfile.at(effort)
    z = zone_of(zones, comp.key)
    if z is None:
        return comp, e
    k = z.scale
    return (
        dataclasses.replace(comp, anchor=(z.x, z.y)),
        dataclasses.replace(e, dead_x=e.dead_x * k, dead_y=e.dead_y * k,
                            soft_x=min(SOFT_MAX, e.soft_x * k), soft_y=min(SOFT_MAX, e.soft_y * k)),
    )
