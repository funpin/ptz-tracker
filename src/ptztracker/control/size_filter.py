"""Размер человека для зума, устойчивый к повороту корпуса и наклону.

Мерка размера (U — «нос → плечи», ширина плеч, корпус) сжимается в разы, когда человек встаёт боком или наклоняется,
и так же быстро возвращается. По-настоящему уменьшиться (отойти от камеры) человек так быстро не может, поэтому рост
размера принимается сразу, а спад — не быстрее U_FALL_RATE; если спад держится дольше U_FALL_CONFIRM_S, размер
действительно другой, и фильтр идёт за ним.
"""

from __future__ import annotations

import math

U_SMOOTH = 0.3  # доля нового замера за один замер
# ln/с: быстрее человек в кадре не уменьшается (от камеры в 14 м шагом 1 м/с — 0,07 ln/с). Живой прогон 2026-11-10:
# поворот боком и наклон сжимали мерку на 1,9 ln за 2,5 с
U_FALL_RATE = 0.15
# с: спад держится дольше — верим ему (повороты и наклоны в живом прогоне — 1–3 с). В плане было 1,5 с: тогда падение
# на 2,5 с, с которого начался этап, принималось бы за настоящее
U_FALL_CONFIRM_S = 3.0
U_FALL_CONFIRMED_RATE = 1.0  # ln/с: подтверждённый спад догоняем быстро, но без скачка


class SizeFilter:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.u: float | None = None
        self._t: float | None = None
        self._fall_t: float | None = None  # с какого момента замер ниже оценки

    def update(self, t: float, u: float) -> float:
        """Новый замер размера u в момент t → оценка размера."""
        u = float(u)
        if self.u is None or self._t is None or u <= 0:
            self.u, self._t, self._fall_t = (u if u > 0 else self.u), t, None
            return self.u if self.u is not None else u
        dt = max(0.0, t - self._t)
        self._t = t
        want = self.u + U_SMOOTH * (u - self.u)
        if u >= self.u:
            self.u, self._fall_t = want, None
            return self.u
        if self._fall_t is None:
            self._fall_t = t
        rate = U_FALL_CONFIRMED_RATE if t - self._fall_t >= U_FALL_CONFIRM_S else U_FALL_RATE
        self.u = max(want, self.u * math.exp(-rate * dt))
        return self.u
