"""Характер движения камеры: от «Мягко» (0) через «Обычно» (0.5) до «Резко» (1)."""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class EffortProfile:
    dead_x: float  # полуширина мёртвой зоны, доли ширины кадра
    dead_y: float
    soft_x: float  # полуширина мягкой зоны
    soft_y: float
    gain: float  # 1/с — как быстро выбирается ошибка
    v_max: float  # fw/s
    a_max: float  # fw/s² — плавность разгона/торможения
    lead: float  # «место для взгляда»: доля кадра на 1 fw/s скорости человека
    zoom_dead: float  # ln
    zoom_gain: float  # 1/с
    zoom_max: float  # ln/с

    @staticmethod
    def at(x: float) -> EffortProfile:
        x = min(1.0, max(0.0, x))
        a, b, k = (_SOFT, _NORMAL, x / 0.5) if x <= 0.5 else (_NORMAL, _SHARP, (x - 0.5) / 0.5)
        if k == 0:
            return a
        if k == 1:
            return b
        return EffortProfile(**{f.name: getattr(a, f.name) + (getattr(b, f.name) - getattr(a, f.name)) * k
                                for f in fields(EffortProfile)})


_SOFT = EffortProfile(0.07, 0.06, 0.25, 0.20, 0.9, 0.30, 0.25, 0.10, 0.15, 0.5, 0.15)
_NORMAL = EffortProfile(0.05, 0.045, 0.22, 0.18, 1.4, 0.50, 0.45, 0.08, 0.12, 0.8, 0.25)
_SHARP = EffortProfile(0.03, 0.03, 0.18, 0.15, 2.2, 0.80, 0.80, 0.06, 0.08, 1.2, 0.40)
