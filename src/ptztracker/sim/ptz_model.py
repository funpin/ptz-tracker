"""Виртуальная PTZ-камера: принимает те же команды, что AW-HE42, и двигается как она.

Скорости (°/с, до 0xD54 от зума не зависят, на теле замедляются), разгон и зум — из замеров живой камеры
(camera.he42), углы обзора — из паспорта (he42.HFOV_*), шкала APC — he42.APC_DEG_PER_UNIT.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from typing import Callable

from ptztracker.camera import he42
from ptztracker.camera.protocol import (
    COLOR_TEMP_MAX,
    GAIN_AUTO,
    GAIN_CODES,
    SHUTTER_STEPS,
    SYNCHRO_MAX,
    SYNCHRO_MIN,
    WB_MODES,
)

TAU_S = he42.RAMP_TAU_S
DEAD_TIME_S = he42.DEAD_TIME_S
PAN_LIMIT = 175.0
TILT_MIN, TILT_MAX = -30.0, 90.0
ABS_MOVE_DEG_S = 60.0  # скорость перехода к абсолютной позиции / пресету
APC_DEG_PER_UNIT = he42.APC_DEG_PER_UNIT
HFOV_WIDE, HFOV_TELE = he42.HFOV_WIDE_DEG, he42.HFOV_TELE_DEG
ZOOM_RATIO_MAX = he42.ZOOM_RATIO
ZOOM_RATE = 0.4  # доля диапазона зума в секунду при переходе к абсолютной позиции
LENS_MIN, LENS_MAX = 0x555, 0xFFF

# Как живая HE42 (площадка 2026-10-29): постоянные °/с до 0xD54, наклон — как поворот; в долях кадра скорость
# растёт при приближении. Выше 0xD54 поворот замедляется (AW-HE40, клуб 2026-10-30, he42.TELE_PAN_DEG_S).
HE42_DEG_S = {50: 0.0, **he42.PAN_DEG_S}
HE42_ZOOM_LN_S = {50: 0.0, **he42.ZOOM_LN_S}


def _signed_interp(table: dict[int, float], code: int) -> float:
    """Значение для кода 01–99 по таблице кодов 50–99 (линейно между точками, зеркально для < 50)."""
    pts = sorted(table.items())
    n = 50 + abs(code - 50)
    for (c0, v0), (c1, v1) in zip(pts, pts[1:], strict=False):
        if c0 <= n <= c1:
            v = v0 + (v1 - v0) * (n - c0) / (c1 - c0)
            return v if code >= 50 else -v
    return 0.0


def he42_deg_s(code: int, zoom_pos: int | None = None) -> float:
    """Скорость pan/tilt (град/с, со знаком) для кода 01–99; zoom_pos — положение #GZ (None — до 0xD54, без
    замедления на теле, he42.TELE_PAN_DEG_S)."""
    v = _signed_interp(HE42_DEG_S, code)
    return v if zoom_pos is None else v * he42.tele_pan_ratio(code, zoom_pos)


def he42_zoom_ln_s(code: int) -> float:
    """Скорость зума (ln масштаба/с, со знаком) для кода 01–99."""
    return _signed_interp(HE42_ZOOM_LN_S, code)


class SimPtz:
    def __init__(
        self,
        # «Speed With Zoom Pos.» ON: камера сама замедляет pan/tilt на теле (в 20 раз). У живой HE42 выключена
        speed_with_zoom: bool = False,
        min_interval: float | None = None,
        # на столько команда может прийти раньше min_interval без отказа «занята»: неровность прихода по сети
        jitter_tolerance: float = 0.005,
        clock: Callable[[], float] = time.monotonic,
        tau: float = TAU_S,
        dead_time: float = DEAD_TIME_S,
        # наклон ÷ поворот на одном коде; у живой HE42 — 1 (2026-10-29). Другое — проверка замера наклона мастером
        tilt_factor: float = 1.0,
    ):
        self.speed_with_zoom = speed_with_zoom
        self.tilt_factor = tilt_factor
        self.tau = tau
        self.dead_time = dead_time
        self._t = 0.0  # время симуляции (сумма dt), по нему отсчитывается мёртвое время
        self._pending: list[tuple[float, str, int]] = []  # (когда вступит, ось, код)
        self.min_interval = min_interval
        self.jitter_tolerance = jitter_tolerance
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
        # питание и изображение — как у живой AW-HE40 (2026-10-30): p3 ~3,5 с после #O1, в ожидании ER2
        self.power_on_s = 4.0
        self._standby = False
        self._power_on_at: float | None = None  # момент #O1 (часы self._clock)
        self.gain_code = 0x08
        self.wb = 0
        self.color_temp = 0x1A
        # затвор, Synchro, уровень чёрного, резкость, скорость пресетов — как у HE42 после сброса ⚠ по документации
        self.shutter, self.synchro, self.pedestal, self.detail, self.preset_speed = 0x0, 0x001, 0x096, 1, 625
        self.scene = 2  # Manual 1 (protocol.SCENES): в симуляторе картинку можно настраивать
        self.osd_open = False  # меню Panasonic на камере (DUS); картинку меню симулятор не рисует
        self.night = False  # День / Ночь (#D6): ночью SimFrameSource отдаёт чёрно-белую картинку
        self.received: deque[str] = deque(maxlen=5000)
        self._target: tuple[float, float] | None = None
        self._zoom_target: float | None = None

    # --- оптика ---------------------------------------------------------

    def zoom_ratio(self) -> float:
        return ZOOM_RATIO_MAX**self.zoom

    def hfov_deg(self) -> float:
        return he42.hfov_deg(self.zoom)

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
                gz = self._zoom_units()
                tgt_p = he42_deg_s(self.pan_code, gz) / div
                tgt_t = he42_deg_s(self.tilt_code, gz) * self.tilt_factor / div
                self.pan_vel += (tgt_p - self.pan_vel) * a
                self.tilt_vel += (tgt_t - self.tilt_vel) * a
            if self._zoom_target is not None:
                d = self._zoom_target - self.zoom
                self.zoom_vel = max(-ZOOM_RATE, min(ZOOM_RATE, d / max(dt, 1e-3)))
                if abs(d) < 1e-4:
                    self._zoom_target = None
            else:
                # zoom — доля диапазона 20×: ln-скорость / ln(20)
                self.zoom_vel += (he42_zoom_ln_s(self.zoom_code) / math.log(ZOOM_RATIO_MAX) - self.zoom_vel) * a
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
                and now - self._last_cmd_t < self.min_interval - self.jitter_tolerance
            ):
                self._last_cmd_t = now
                return "eR2:" + cmd.lstrip("#")
            self._last_cmd_t = now
            try:
                return self._dispatch(cmd)
            except ValueError:
                return "eR3:" + cmd.lstrip("#")

    def _pan_units(self) -> tuple[int, int]:
        p = he42.APC_CENTER + round(self.pan_deg / APC_DEG_PER_UNIT)
        t = he42.APC_CENTER + round(self.tilt_deg / APC_DEG_PER_UNIT)
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

    @property
    def power(self) -> str:
        if self._standby:
            return "standby"
        if self._power_on_at is not None and self._clock() - self._power_on_at < self.power_on_s:
            return "switching"
        return "on"

    def _dispatch(self, cmd: str) -> str:
        if cmd == "#O":
            return {"on": "p1", "standby": "p0", "switching": "p3"}[self.power]
        if cmd == "#O0":
            self._standby, self._power_on_at = True, None
            self.pan_code = self.tilt_code = self.zoom_code = 50
            return "p0"
        if cmd == "#O1":
            if self._standby:
                self._standby, self._power_on_at = False, self._clock()
            return "p1"
        if self.power != "on":
            return "ER2:" + cmd.lstrip("#").split(":")[0]
        if cmd == "QGU":
            return f"OGU:{self.gain_code:02X}"
        if m := re.fullmatch(r"OGU:([0-9A-F]{2})", cmd):
            v = int(m[1], 16)
            if v != GAIN_AUTO and v not in GAIN_CODES:
                raise ValueError(cmd)
            self.gain_code = v
            return cmd
        if cmd == "QAW":
            return f"OAW:{self.wb}"
        if m := re.fullmatch(r"OAW:([0-9A-F])", cmd):
            if int(m[1], 16) not in WB_MODES:
                raise ValueError(cmd)
            self.wb = int(m[1], 16)
            return cmd
        if cmd == "QSD:B1":
            return f"OSD:B1:{self.color_temp:03X}"
        if m := re.fullmatch(r"OSD:B1:([0-9A-F]{3})", cmd):
            if int(m[1], 16) > COLOR_TEMP_MAX:
                raise ValueError(cmd)
            self.color_temp = int(m[1], 16)
            return cmd
        if cmd == "QUS":
            return f"OUS:{int(self.osd_open)}"
        if m := re.fullmatch(r"DUS:([01])", cmd):
            self.osd_open = m[1] == "1"
            return cmd
        if cmd in ("DUP", "DDW", "DIT", "DPG"):
            return f"{cmd}:1"
        if cmd == "QSH":
            return f"OSH:{self.shutter:X}"
        if m := re.fullmatch(r"OSH:([0-9A-F])", cmd):
            if int(m[1], 16) not in SHUTTER_STEPS:
                raise ValueError(cmd)
            self.shutter = int(m[1], 16)
            return cmd
        if cmd == "QMS":
            return f"OMS:{self.synchro:03X}"
        if m := re.fullmatch(r"OMS:([0-9A-F]{3})", cmd):
            if not SYNCHRO_MIN <= int(m[1], 16) <= SYNCHRO_MAX:
                raise ValueError(cmd)
            self.synchro = int(m[1], 16)
            return cmd
        if cmd == "QTP":
            return f"OTP:{self.pedestal:03X}"
        if m := re.fullmatch(r"OTP:([0-9A-F]{3})", cmd):
            if int(m[1], 16) > 0x12C:
                raise ValueError(cmd)
            self.pedestal = int(m[1], 16)
            return cmd
        if cmd == "QDT":
            return f"ODT:{self.detail}"
        if m := re.fullmatch(r"ODT:([0-2])", cmd):
            self.detail = int(m[1])
            return cmd
        if cmd == "QSF":
            return f"OSF:{self.scene}"
        if m := re.fullmatch(r"XSF:([1-4])", cmd):
            self.scene = int(m[1])
            return f"OSF:{self.scene}"
        if cmd == "#UPVS":
            return f"uPVS{self.preset_speed:03d}"
        if m := re.fullmatch(r"#UPVS(\d{3})", cmd):
            if not 250 <= int(m[1]) <= 999:
                raise ValueError(cmd)
            self.preset_speed = int(m[1])
            return f"uPVS{m[1]}"
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
            p = (int(m[1], 16) - he42.APC_CENTER) * APC_DEG_PER_UNIT
            t = (int(m[2], 16) - he42.APC_CENTER) * APC_DEG_PER_UNIT
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
        if cmd == "#D6":
            return f"d6{int(self.night)}"
        if m := re.fullmatch(r"#D6([01])", cmd):
            self.night = m[1] == "1"
            return f"d6{m[1]}"
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
