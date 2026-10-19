"""Модель камеры для регулятора: «код скорости ↔ скорость картинки», зум и динамика разгона.

Единицы: pan/tilt — доли ширины кадра в секунду (fw/s), зум — ln(масштаба) в секунду.
Числа по умолчанию — из замеров живой AW-HE42 (§7 спеки); мастер калибровки их уточняет.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from ptztracker.camera import he42

MOVING_THRESHOLD = 0.005  # быстрее этого (fw/s или ln/s) — код «реально двигает»


def _interp_curve(curve: list[tuple[int, float]], n: int) -> float:
    """Скорость для кода n ≥ 50 по точкам кривой (с точкой (50, 0)), линейно."""
    pts = [(50, 0.0)] + [p for p in curve if p[0] > 50]
    if n >= pts[-1][0]:
        return pts[-1][1]
    for (c0, v0), (c1, v1) in zip(pts, pts[1:]):
        if c0 <= n <= c1:
            return v0 + (v1 - v0) * (n - c0) / (c1 - c0)
    return 0.0


def _code_for(speed_of, v: float, current: int, hysteresis: float) -> int:
    """Минимальный код, дающий |v|; с порогом «не двигаться» и гистерезисом против дребезга."""
    a = abs(v)
    v_min = next((speed_of(n) for n in range(51, 100) if speed_of(n) > MOVING_THRESHOLD), MOVING_THRESHOLD)
    if a < 0.5 * v_min:
        return 50
    if current != 50 and (current > 50) == (v > 0):
        cur_n = 50 + abs(current - 50)
        if abs(speed_of(cur_n) - a) <= hysteresis * a:
            return current
    n = next((n for n in range(51, 100) if speed_of(n) >= a), 99)
    return n if v > 0 else 100 - n


class SpeedTable:
    """Скорость pan/tilt (fw/s) по коду для нескольких положений зума (#GZ, 0x555–0xFFF)."""

    def __init__(self, points: dict[int, list[tuple[int, float]]]):
        self.points = {int(z): sorted((int(c), float(v)) for c, v in curve) for z, curve in points.items()}
        self._zooms = sorted(self.points)

    def _speed_pos(self, n: int, zoom: int) -> float:
        zs = self._zooms
        if len(zs) == 1 or zoom <= zs[0]:
            return _interp_curve(self.points[zs[0]], n)
        if zoom >= zs[-1]:
            return _interp_curve(self.points[zs[-1]], n)
        i = bisect.bisect_right(zs, zoom)
        z0, z1 = zs[i - 1], zs[i]
        a, b = _interp_curve(self.points[z0], n), _interp_curve(self.points[z1], n)
        return a + (b - a) * (zoom - z0) / (z1 - z0)

    def speed(self, code: int, zoom: int) -> float:
        n = 50 + abs(code - 50)
        v = self._speed_pos(n, zoom)
        return v if code >= 50 else -v

    def code_for(self, v: float, zoom: int, current: int = 50, hysteresis: float = 0.15) -> int:
        return _code_for(lambda n: self._speed_pos(n, zoom), v, current, hysteresis)

    def to_json(self) -> dict:
        return {str(z): [[c, v] for c, v in curve] for z, curve in self.points.items()}

    @classmethod
    def from_json(cls, d: dict) -> SpeedTable:
        return cls({int(z): [tuple(p) for p in curve] for z, curve in d.items()})


class ZoomTable:
    """Скорость зума (ln масштаба в секунду) по коду #Z."""

    def __init__(self, points: list[tuple[int, float]]):
        self.points = sorted((int(c), float(v)) for c, v in points)

    def rate(self, code: int) -> float:
        n = 50 + abs(code - 50)
        v = _interp_curve(self.points, n)
        return v if code >= 50 else -v

    def code_for(self, rate: float, current: int = 50, hysteresis: float = 0.15) -> int:
        return _code_for(lambda n: _interp_curve(self.points, n), rate, current, hysteresis)

    def to_json(self) -> list:
        return [[c, v] for c, v in self.points]


class CameraDynamics:
    """Как картинка реально движется после команды: мёртвое время + плавный разгон (инерционное звено).

    Нужна, чтобы знать, насколько камера уже сдвинула картинку, пока кадр шёл по сети.
    """

    KEEP_S = 10.0

    def __init__(self, dead_time: float = he42.DEAD_TIME_S, tau: float = he42.RAMP_TAU_S):
        self.dead_time = dead_time
        self.tau = tau
        self._base = (-1e9, 0.0, 0.0, 0.0)  # (t, x, v, цель) — свёрнутая история
        self._cmds: list[tuple[float, float]] = []  # (когда вступает, цель)

    def command(self, t: float, v: float) -> None:
        if self._cmds and abs(self._cmds[-1][1] - v) < 1e-12:
            return
        self._cmds.append((t + self.dead_time, v))
        self._fold(t - self.KEEP_S)

    def _advance(self, t0: float, x: float, v: float, target: float, t1: float) -> tuple[float, float]:
        dt = t1 - t0
        if dt <= 0:
            return x, v
        if self.tau <= 0:
            return x + target * dt, target
        e = math.exp(-dt / self.tau)
        return x + target * dt + (v - target) * self.tau * (1 - e), target + (v - target) * e

    def _state(self, t: float) -> tuple[float, float]:
        tb, x, v, target = self._base
        cur_t = tb
        for t_eff, new_target in self._cmds:
            if t_eff > t:
                break
            x, v = self._advance(cur_t, x, v, target, t_eff)
            cur_t, target = t_eff, new_target
        return self._advance(cur_t, x, v, target, t)

    def _fold(self, before: float) -> None:
        while self._cmds and self._cmds[0][0] < before:
            t_eff, new_target = self._cmds.pop(0)
            tb, x, v, target = self._base
            x, v = self._advance(tb, x, v, target, t_eff) if tb > -1e8 else (x, v)
            self._base = (t_eff, x, v, new_target)

    def velocity(self, t: float) -> float:
        return self._state(t)[1]

    def position(self, t: float) -> float:
        """Накопленное смещение картинки с начала работы. Верно для последних KEEP_S секунд:
        более старые команды свёрнуты (отсюда нельзя брать «смещение с момента 0»)."""
        return self._state(t)[0]

    def displacement(self, t0: float, t1: float) -> float:
        return self._state(t1)[0] - self._state(t0)[0]


@dataclass
class Calibration:
    pan: SpeedTable
    zoom: ZoomTable
    dead_time: float = he42.DEAD_TIME_S
    tau: float = he42.RAMP_TAU_S
    video_latency: dict[str, float] = field(default_factory=lambda: dict(he42.VIDEO_LATENCY_S))
    created: str | None = None

    def to_json(self) -> dict:
        return {
            "pan": self.pan.to_json(),
            "zoom": self.zoom.to_json(),
            "dead_time": self.dead_time,
            "tau": self.tau,
            "video_latency": dict(self.video_latency),
            "created": self.created,
        }

    @classmethod
    def from_json(cls, d: dict) -> Calibration:
        return cls(
            pan=SpeedTable.from_json(d["pan"]),
            zoom=ZoomTable([tuple(p) for p in d["zoom"]]),
            dead_time=float(d.get("dead_time", he42.DEAD_TIME_S)),
            tau=float(d.get("tau", he42.RAMP_TAU_S)),
            video_latency={k: float(v) for k, v in (d.get("video_latency") or {}).items()}
            or dict(he42.VIDEO_LATENCY_S),
            created=d.get("created"),
        )


def default_calibration(safety: float = 1.0) -> Calibration:
    """Средние значения до калибровки: одна таблица для всех зумов (камера сама замедляет pan на теле).

    safety > 1 — считать камеру быстрее замеров: ошибиться в «медленную» сторону безопасно
    (регулятор устойчив до ρ≈0.5), а в «быструю» — раскачка (устойчив до ρ≈1.7).
    """
    pan = [(c, v * safety) for c, v in he42.PAN_FW_S.items()]
    return Calibration(pan=SpeedTable({he42.MEASURED_AT_ZOOM: pan}), zoom=ZoomTable(list(he42.ZOOM_LN_S.items())))
