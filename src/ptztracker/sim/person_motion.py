"""Как двигается человек: переминание, ходьба с разгоном, качание шага. Общее для сцены и виртуального стенда."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

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


WALK_RAMP_S = 0.5  # человек разгоняется и тормозит не мгновенно
WALK_BOB_M = 0.025  # качание корпуса на ходу, метры


@dataclass(frozen=True)
class Leg:
    """Отрезок маршрута: стоит (x0 == x1, шатается на sway метров) или идёт из x0 в x1."""

    t0: float
    length: float
    x0: float
    x1: float
    sway: float = 0.0
    phase: float = 0.0

    @property
    def walking(self) -> bool:
        return self.x0 != self.x1


class RandomRoute:
    """Случайный маршрут докладчика: куда, как быстро и как далеко идёт, сколько стоит и как сильно шатается.

    Один seed — один маршрут, разные seed — разные маршруты, чтобы настройки не подгонялись под один заученный путь.
    swaying=True — докладчик больше стоит и сильно шатается из стороны в сторону.
    """

    def __init__(self, seed: int, half_width_m: float = 2.5, swaying: bool = False):
        self.rng = random.Random(seed)
        self.half = half_width_m
        self.swaying = swaying
        self.legs: list[Leg] = []
        self._end = 0.0
        self._x = 0.0
        self._add_stand()

    def _sway_amp(self) -> float:
        roll = self.rng.random()
        strong = 0.8 if self.swaying else 0.3
        if roll < strong:
            return self.rng.uniform(0.25, 0.38)  # сильно шатается: на «Поясе» это ±0,15…0,22 кадра
        if roll < strong + (1 - strong) / 2:
            return self.rng.uniform(0.10, 0.18)
        return self.rng.uniform(0.03, 0.08)

    def _add_stand(self) -> None:
        length = self.rng.uniform(6.0, 15.0) if self.swaying else self.rng.uniform(1.5, 9.0)
        self.legs.append(Leg(self._end, length, self._x, self._x, self._sway_amp(), self.rng.uniform(0.0, 6.28)))
        self._end += length

    def _add_walk(self) -> None:
        dist = self.rng.uniform(0.3, 0.6) if self.rng.random() < 0.2 else self.rng.uniform(0.6, 4.5)
        side = self.rng.choice((-1, 1))
        if abs(self._x + side * dist) > self.half:
            side = -side
        target = max(-self.half, min(self.half, self._x + side * dist))
        speed = self.rng.uniform(0.25, 1.4)
        length = max(1.2, abs(target - self._x) / speed + WALK_RAMP_S)
        self.legs.append(Leg(self._end, length, self._x, target))
        self._end += length
        self._x = target

    def leg_at(self, t: float) -> Leg:
        while t >= self._end:
            self._add_walk() if not self.legs[-1].walking else self._add_stand()
        return next(leg for leg in reversed(self.legs) if t >= leg.t0)

    def x(self, t: float) -> float:
        """Где человек, метры от середины сцены."""
        leg = self.leg_at(t)
        u = t - leg.t0
        fade = phase_fade(u, leg.length)
        if not leg.walking:
            return leg.x0 + fade * sway(t, leg.sway, leg.phase)
        v = abs(leg.x1 - leg.x0) / (leg.length - WALK_RAMP_S)
        moved = walk_position(u, 0.0, leg.length, v, WALK_RAMP_S)
        return leg.x0 + moved * (1 if leg.x1 > leg.x0 else -1) + fade * WALK_BOB_M * walk_bob(t)

    def walking(self, t: float) -> bool:
        return self.leg_at(t).walking

    def walk_fade(self, t: float) -> float:
        leg = self.leg_at(t)
        return phase_fade(t - leg.t0, leg.length) if leg.walking else 0.0
