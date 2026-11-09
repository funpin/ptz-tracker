"""Как двигается человек: переминание, ходьба с разгоном, качание шага. Общее для сцены и виртуального стенда."""

from __future__ import annotations

import math

# Перенос веса с ноги на ногу: 0,35–1,35 Гц по задачам из литературы (спека), размах — по записям с площадки
SWAY_PARTS = ((0.6, 0.31, 0.0), (0.3, 0.53, 1.1), (0.1, 1.2, 2.3))  # (доля, Гц, фаза)
STEP_HZ = 1.8  # шагов в секунду: боковое качание корпуса на ходу


def sway(t: float, amp: float, seed: float = 0.0) -> float:
    """Переминание: непериодическая сумма синусов, размах не больше ±amp."""
    return amp * sum(a * math.sin(2 * math.pi * f * t + p + seed) for a, f, p in SWAY_PARTS)


def walk_position(t: float, t0: float, t1: float, v: float, ramp: float = 0.5) -> float:
    """Пройденный путь при трапеции скорости: разгон и торможение за ramp, между ними скорость v."""
    if t <= t0:
        return 0.0
    length = t1 - t0
    u = min(t - t0, length)
    if u < ramp:
        return 0.5 * v / ramp * u * u
    if u <= length - ramp:
        return v * (u - 0.5 * ramp)
    rest = length - u
    return v * (length - ramp) - 0.5 * v / ramp * rest * rest


def walk_bob(t: float) -> float:
    """Боковое качание корпуса на ходу, от −1 до 1."""
    return math.sin(2 * math.pi * STEP_HZ * t)


def phase_fade(u: float, length: float, edge: float = 0.5) -> float:
    """Вес 0…1 внутри отрезка: нарастает и спадает за edge, чтобы движения стыковались без скачка."""
    return max(0.0, min(1.0, u / edge, (length - u) / edge))
