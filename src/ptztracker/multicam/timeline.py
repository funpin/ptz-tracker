"""Лента состояний одной камеры для «Сводки»: что было за последние WINDOW_S (ведёт, ищет, вручную, эфир)
и часы — сколько камера в эфире или ведёт цель без потери. Без Qt; окно пишет раз в тик статуса."""

from __future__ import annotations

from dataclasses import dataclass

WINDOW_S = 600.0  # дорожка — последние 10 минут


@dataclass(frozen=True)
class Segment:
    state: str  # on | lost | off | standby | no_analysis | air
    f0: float  # доля окна: 0 — WINDOW_S назад, 1 — сейчас
    f1: float


class StateTimeline:
    def __init__(self) -> None:
        self._runs: list[tuple[float, str]] = []  # (начало, состояние) — только смены
        self._air: list[tuple[float, bool]] = []
        self._start: float | None = None
        self._air_total = 0.0
        self.losses = 0  # сколько раз цель терялась (ведёт → ищет) за сеанс

    def record(self, t: float, state: str, on_air: bool) -> None:
        if self._start is None:
            self._start = t
        if not self._runs or self._runs[-1][1] != state:
            if self._runs and self._runs[-1][1] == "on" and state == "lost":
                self.losses += 1
            self._runs.append((t, state))
        if not self._air or self._air[-1][1] != on_air:
            if self._air and self._air[-1][1]:
                self._air_total += t - self._air[-1][0]
            self._air.append((t, on_air))
        cut = t - WINDOW_S
        while len(self._runs) > 1 and self._runs[1][0] <= cut:  # первая смена — уже за окном
            self._runs.pop(0)
        while len(self._air) > 1 and self._air[1][0] <= cut:
            self._air.pop(0)

    def _frac(self, t: float, now: float) -> float:
        lo = max(now - WINDOW_S, self._start if self._start is not None else now)
        span = now - lo
        return 1.0 if span <= 0 else max(0.0, min(1.0, (t - lo) / span))

    def segments(self, now: float) -> list[Segment]:
        out = []
        for i, (t, st) in enumerate(self._runs):
            end = self._runs[i + 1][0] if i + 1 < len(self._runs) else now
            f0, f1 = self._frac(t, now), self._frac(end, now)
            if f1 > f0:
                out.append(Segment(st, f0, f1))
        return out

    def air(self, now: float) -> list[Segment]:
        out = []
        for i, (t, on) in enumerate(self._air):
            if not on:
                continue
            end = self._air[i + 1][0] if i + 1 < len(self._air) else now
            f0, f1 = self._frac(t, now), self._frac(end, now)
            if f1 > f0:
                out.append(Segment("air", f0, f1))
        return out

    def clock(self, now: float) -> tuple[str, float | None]:
        """Что показать крупными цифрами: в эфире — сколько в эфире; иначе ведёт или ищет — сколько длится."""
        if self._air and self._air[-1][1]:
            return "air", now - self._air[-1][0]
        if not self._runs:
            return "off", None
        t, st = self._runs[-1]
        if st in ("on", "lost"):
            return st, now - t
        return st, None

    def span(self, now: float) -> float:
        """Сколько секунд покрывает дорожка: WINDOW_S или меньше, если камера открыта недавно."""
        return 0.0 if self._start is None else min(WINDOW_S, now - self._start)

    def air_total(self, now: float) -> float:
        cur = now - self._air[-1][0] if self._air and self._air[-1][1] else 0.0
        return self._air_total + cur
