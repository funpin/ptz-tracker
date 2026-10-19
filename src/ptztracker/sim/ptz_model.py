"""Виртуальная PTZ-камера: принимает те же команды, что AW-HE42, и двигается с инерцией.

Таблица скоростей и шкала APC здесь условные (только для симулятора);
реальные значения даст диагностика на живой камере.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from typing import Callable

TAU_S = 0.37  # разгон/торможение живой AW-HE42: до 90% скорости ~0,85 с
DEAD_TIME_S = 0.08  # команда начинает действовать не сразу
PAN_LIMIT = 175.0
TILT_MIN, TILT_MAX = -30.0, 90.0
# Скорость pan/tilt по коду (град/с при широком угле), по замерам живой AW-HE42 (§7 спеки):
# 55 почти стоит, 60 ≈ 0,034 fw/s, 70 ≈ 0,33 fw/s, 80 ≈ 0,50 fw/s при зуме 0x73F.
HE42_DEG_S = {50: 0.0, 53: 0.0, 55: 0.05, 58: 0.5, 60: 1.24, 62: 2.2, 65: 4.5, 68: 8.5,
              70: 12.0, 75: 15.0, 80: 18.0, 85: 24.0, 90: 35.0, 99: 60.0}
_TABLE = sorted(HE42_DEG_S.items())
ABS_MOVE_DEG_S = 60.0  # скорость перехода к абсолютной позиции / пресету
APC_DEG_PER_UNIT = 0.0075
HFOV_WIDE, HFOV_TELE = 62.1, 3.3
ZOOM_RATIO_MAX = 20.0
ZOOM_RATE = 0.4  # доля диапазона зума в секунду на максимальной скорости
LENS_MIN, LENS_MAX = 0x555, 0xFFF


def he42_deg_s(code: int) -> float:
    """Скорость (град/с, со знаком) для кода 01–99 по таблице HE42 (линейно между точками)."""
    n = 50 + abs(code - 50)
    for (c0, v0), (c1, v1) in zip(_TABLE, _TABLE[1:]):
        if c0 <= n <= c1:
            v = v0 + (v1 - v0) * (n - c0) / (c1 - c0)
            return v if code >= 50 else -v
    return 0.0


def _code_rate(code: int, power: float) -> float:
    n = code - 50
    return math.copysign((abs(n) / 49) ** power, n) if n else 0.0


class SimPtz:
    def __init__(
        self,
        speed_with_zoom: bool = True,
        min_interval: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        tau: float = TAU_S,
        dead_time: float = DEAD_TIME_S,
    ):
        self.speed_with_zoom = speed_with_zoom
        self.tau = tau
        self.dead_time = dead_time
        self._t = 0.0  # время симуляции (сумма dt), по нему отсчитывается мёртвое время
        self._pending: list[tuple[float, str, int]] = []  # (когда вступит, ось, код)
        self.min_interval = min_interval
        self._clock = clock
        self._lock = threading.RLock()
        self._last_cmd_t: float | None = None
        self.pan_deg = 0.0
        self.tilt_deg = 0.0
        self.zoom = 0.0
        self.pan_vel = 0.0
        self.tilt_vel = 0.0
        self.zoom_vel = 0.0
        self.pan_code = self.tilt_code = self.zoom_code = 50
        self.focus_auto = True
        self.focus_pos = 0x800
        self.iris_auto = True
        self.iris_pos = 0x800
        self.presets: dict[int, tuple[float, float, float]] = {}
        self.received: deque[str] = deque(maxlen=200)
        self._target: tuple[float, float] | None = None
        self._zoom_target: float | None = None

    # --- оптика ---------------------------------------------------------

    def zoom_ratio(self) -> float:
        return ZOOM_RATIO_MAX**self.zoom

    def hfov_deg(self) -> float:
        return HFOV_WIDE * (HFOV_TELE / HFOV_WIDE) ** self.zoom

    # --- физика ---------------------------------------------------------

    def _set_code(self, axis: str, code: int) -> None:
        """Скоростная команда вступает в силу через dead_time."""
        if self.dead_time <= 0:
            setattr(self, axis, code)
        else:
            self._pending.append((self._t + self.dead_time, axis, code))

    def update(self, dt: float) -> None:
        with self._lock:
            self._t += dt
            while self._pending and self._pending[0][0] <= self._t:
                _, axis, code = self._pending.pop(0)
                setattr(self, axis, code)
            a = 1.0 - math.exp(-dt / self.tau)
            if self._target is not None:
                tp, tt = self._target
                want_p = max(-ABS_MOVE_DEG_S, min(ABS_MOVE_DEG_S, (tp - self.pan_deg) / max(dt, 1e-3)))
                want_t = max(-ABS_MOVE_DEG_S, min(ABS_MOVE_DEG_S, (tt - self.tilt_deg) / max(dt, 1e-3)))
                self.pan_vel, self.tilt_vel = want_p, want_t  # абсолютные перемещения без инерции
                if abs(tp - self.pan_deg) < 1e-3 and abs(tt - self.tilt_deg) < 1e-3:
                    self._target = None
            else:
                div = self.zoom_ratio() if self.speed_with_zoom else 1.0
                tgt_p = he42_deg_s(self.pan_code) / div
                tgt_t = he42_deg_s(self.tilt_code) / div
                self.pan_vel += (tgt_p - self.pan_vel) * a
                self.tilt_vel += (tgt_t - self.tilt_vel) * a
            if self._zoom_target is not None:
                d = self._zoom_target - self.zoom
                self.zoom_vel = max(-ZOOM_RATE, min(ZOOM_RATE, d / max(dt, 1e-3)))
                if abs(d) < 1e-4:
                    self._zoom_target = None
            else:
                self.zoom_vel += (ZOOM_RATE * _code_rate(self.zoom_code, 1.5) - self.zoom_vel) * a
            self.pan_deg = max(-PAN_LIMIT, min(PAN_LIMIT, self.pan_deg + self.pan_vel * dt))
            self.tilt_deg = max(TILT_MIN, min(TILT_MAX, self.tilt_deg + self.tilt_vel * dt))
            self.zoom = max(0.0, min(1.0, self.zoom + self.zoom_vel * dt))

    # --- команды --------------------------------------------------------

    def handle(self, cmd: str) -> str:
        with self._lock:
            self.received.append(cmd)
            now = self._clock()
            if (
                self.min_interval is not None
                and self._last_cmd_t is not None
                and now - self._last_cmd_t < self.min_interval - 0.005
            ):
                self._last_cmd_t = now
                return "eR2:" + cmd.lstrip("#")
            self._last_cmd_t = now
            try:
                return self._dispatch(cmd)
            except ValueError:
                return "eR3:" + cmd.lstrip("#")

    def _pan_units(self) -> tuple[int, int]:
        p = 0x8000 + round(self.pan_deg / APC_DEG_PER_UNIT)
        t = 0x8000 + round(self.tilt_deg / APC_DEG_PER_UNIT)
        return max(0, min(0xFFFF, p)), max(0, min(0xFFFF, t))

    def _zoom_units(self) -> int:
        return round(LENS_MIN + self.zoom * (LENS_MAX - LENS_MIN))

    def _speed_arg(self, s: str) -> int:
        v = int(s)
        if not 1 <= v <= 99:
            raise ValueError(s)
        return v

    def _lens_arg(self, s: str) -> int:
        v = int(s, 16)
        if not LENS_MIN <= v <= LENS_MAX:
            raise ValueError(s)
        return v

    def _dispatch(self, cmd: str) -> str:
        if cmd == "#O":
            return "p1"
        if cmd == "QID":
            return "OID:AW-HE42"
        if cmd.startswith("OSE:"):
            return cmd
        if m := re.fullmatch(r"#PTS(\d\d)(\d\d)", cmd):
            p, t = self._speed_arg(m[1]), self._speed_arg(m[2])
            self._set_code("pan_code", p)
            self._set_code("tilt_code", t)
            self._target = None
            return f"pTS{m[1]}{m[2]}"
        if m := re.fullmatch(r"#P(\d\d)", cmd):
            self._set_code("pan_code", self._speed_arg(m[1]))
            self._target = None
            return f"pS{m[1]}"
        if m := re.fullmatch(r"#T(\d\d)", cmd):
            self._set_code("tilt_code", self._speed_arg(m[1]))
            self._target = None
            return f"tS{m[1]}"
        if m := re.fullmatch(r"#Z(\d\d)", cmd):
            self._set_code("zoom_code", self._speed_arg(m[1]))
            self._zoom_target = None
            return f"zS{m[1]}"
        if cmd == "#APC":
            p, t = self._pan_units()
            return f"aPC{p:04X}{t:04X}"
        if m := re.fullmatch(r"#APC([0-9A-Fa-f]{4})([0-9A-Fa-f]{4})", cmd):
            p = (int(m[1], 16) - 0x8000) * APC_DEG_PER_UNIT
            t = (int(m[2], 16) - 0x8000) * APC_DEG_PER_UNIT
            self._target = (max(-PAN_LIMIT, min(PAN_LIMIT, p)), max(TILT_MIN, min(TILT_MAX, t)))
            self.pan_code = self.tilt_code = 50
            return f"aPC{m[1].upper()}{m[2].upper()}"
        if cmd == "#GZ":
            return f"gz{self._zoom_units():03X}"
        if m := re.fullmatch(r"#AXZ([0-9A-Fa-f]{3})", cmd):
            v = self._lens_arg(m[1])
            self._zoom_target = (v - LENS_MIN) / (LENS_MAX - LENS_MIN)
            self.zoom_code = 50
            return f"axz{v:03X}"
        if m := re.fullmatch(r"#D1([01])", cmd):
            self.focus_auto = m[1] == "1"
            return f"d1{m[1]}"
        if m := re.fullmatch(r"#D3([01])", cmd):
            self.iris_auto = m[1] == "1"
            return f"d3{m[1]}"
        if m := re.fullmatch(r"#F(\d\d)", cmd):
            self._speed_arg(m[1])
            return f"fS{m[1]}"
        if cmd == "#GF":
            return f"gf{self.focus_pos:03X}"
        if m := re.fullmatch(r"#AXI([0-9A-Fa-f]{3})", cmd):
            self.iris_pos = self._lens_arg(m[1])
            return f"axi{self.iris_pos:03X}"
        if cmd == "#GI":
            return f"gi{self.iris_pos:03X}"
        if m := re.fullmatch(r"#R(\d\d)", cmd):
            n = int(m[1])
            if n in self.presets:
                p, t, z = self.presets[n]
                self._target = (p, t)
                self._zoom_target = z
                self.pan_code = self.tilt_code = self.zoom_code = 50
            return f"s{m[1]}"
        if m := re.fullmatch(r"#M(\d\d)", cmd):
            self.presets[int(m[1])] = (self.pan_deg, self.tilt_deg, self.zoom)
            return f"s{m[1]}"
        return "eR1:" + cmd.lstrip("#")
