"""Модель камеры для регулятора: «код скорости ↔ скорость картинки», зум и динамика разгона.

Единицы: pan/tilt — доли ширины кадра в секунду (fw/s) на данном зуме, зум — ln(масштаба) в секунду.
Числа по умолчанию — из замеров живой AW-HE42 (§7 спеки, he42.PAN_DEG_S в °/с); мастер калибровки их уточняет.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from ptztracker.camera import he42
from ptztracker.camera import protocol as P

MOVING_THRESHOLD = 0.005  # быстрее этого (fw/s или ln/s) — код «реально двигает»
# Гистерезис кода pan/tilt: прежний код держится, пока его скорость в пределах этой доли от нужной. Меньше шага
# между кодами живой HE42 у 65–75 (~10%; см. DECISIONS). Было 0,15 — при выдуманной таблице с шагом ~5%.
PT_HYSTERESIS = 0.10


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

    def code_for(self, v: float, zoom: int, current: int = 50, hysteresis: float = PT_HYSTERESIS) -> int:
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
        return self._full_state(t)[:2]

    def _full_state(self, t: float) -> tuple[float, float, float]:
        """(положение, скорость, цель) в момент t."""
        tb, x, v, target = self._base
        cur_t = tb
        for t_eff, new_target in self._cmds:
            if t_eff > t:
                break
            x, v = self._advance(cur_t, x, v, target, t_eff)
            cur_t, target = t_eff, new_target
        x, v = self._advance(cur_t, x, v, target, t)
        return x, v, target

    def coast(self, t: float) -> float:
        """Тормозной путь: сколько картинка ещё сдвинется, если в момент t скомандовать «стоп».

        Уже отправленные команды доходят за мёртвое время, потом скорость гаснет с τ (путь v·τ).
        """
        x0, v, target = self._full_state(t)
        x, cur_t = x0, t
        for t_eff, new_target in self._cmds:
            if t_eff <= t:
                continue
            x, v = self._advance(cur_t, x, v, target, t_eff)
            cur_t, target = t_eff, new_target
        x, v = self._advance(cur_t, x, v, target, t + self.dead_time)
        return x - x0 + v * self.tau

    def _fold(self, before: float) -> None:
        while self._cmds and self._cmds[0][0] < before:
            t_eff, new_target = self._cmds.pop(0)
            tb, x, v, target = self._base
            x, v = self._advance(tb, x, v, target, t_eff) if tb > -1e8 else (x, v)
            self._base = (t_eff, x, v, new_target)

    def shift(self, dx: float) -> None:
        """Сдвинуть всю историю положения на dx (поправка по замеру; скорость и команды не меняются)."""
        tb, x, v, target = self._base
        self._base = (tb, x + dx, v, target)

    def velocity(self, t: float) -> float:
        return self._state(t)[1]

    def position(self, t: float) -> float:
        """Накопленное смещение картинки с начала работы. Верно для последних KEEP_S секунд:
        более старые команды свёрнуты (отсюда нельзя брать «смещение с момента 0»)."""
        return self._state(t)[0]

    def displacement(self, t0: float, t1: float) -> float:
        return self._state(t1)[0] - self._state(t0)[0]


def zoom_ln(pos: int) -> float:
    """ln масштаба зума от широкого угла по положению #GZ (Panasonic 0x555–0xFFF), см. he42.ZOOM_RATIO."""
    return (pos - P.LENS_MIN) / (P.LENS_MAX - P.LENS_MIN) * math.log(he42.ZOOM_RATIO)


# Пределы поправки скорости по ходу слежения: дальше — это уже не поправка, а другая камера (нужна калибровка).
SCALE_MIN, SCALE_MAX = 0.5, 2.0

DEFAULT_VIDEO_LATENCY_S = 0.15  # задержка видео, если для этого вида потока нет замера


@dataclass
class Calibration:
    pan: SpeedTable
    zoom: ZoomTable
    dead_time: float = he42.DEAD_TIME_S
    tau: float = he42.RAMP_TAU_S
    video_latency: dict[str, float] = field(default_factory=lambda: dict(he42.VIDEO_LATENCY_S))
    created: str | None = None
    # Поправка таблиц по ходу слежения (control.speed_adapt): камера быстрее таблицы в столько раз.
    # Таблицы не меняются; скорость камеры и код для скорости — только через методы ниже. В to_json поправки
    # нет: единственное место хранения — CameraConfig.speed_scale (настройки), в записи — каждый такт control.
    pan_scale: float = 1.0
    tilt_scale: float = 1.0
    zoom_scale: float = 1.0

    def axis_scale(self, axis: str) -> float:
        return self.tilt_scale if axis == "tilt" else self.pan_scale

    def pan_fw(self, code: int, zoom: int, axis: str = "pan") -> float:
        """Скорость камеры (fw/s, со знаком) по коду pan/tilt — таблица с поправкой оси."""
        return self.pan.speed(code, zoom) * self.axis_scale(axis)

    def pan_code(self, v: float, zoom: int, current: int = 50, axis: str = "pan") -> int:
        """Код pan/tilt для скорости v (fw/s) с учётом поправки оси (с гистерезисом от current)."""
        return self.pan.code_for(v / self.axis_scale(axis), zoom, current=current)

    def pan_max_fw(self, zoom: int, axis: str = "pan") -> float:
        """Самая большая скорость камеры (fw/s) на этом зуме — код 99 с поправкой оси."""
        return self.pan_fw(99, zoom, axis)

    def zoom_rate(self, code: int) -> float:
        """Скорость зума (ln/с, со знаком) по коду #Z — таблица с поправкой."""
        return self.zoom.rate(code) * self.zoom_scale

    def zoom_code(self, rate: float, current: int = 50) -> int:
        return self.zoom.code_for(rate / self.zoom_scale, current=current)

    @property
    def speed_scale(self) -> dict[str, float]:
        return {"pan": self.pan_scale, "tilt": self.tilt_scale, "zoom": self.zoom_scale}

    def set_speed_scale(self, d) -> None:
        """Поправка из настроек/записи; неправильные значения пропускаются, остальное — в SCALE_MIN…SCALE_MAX."""
        if not isinstance(d, dict):
            return
        for axis in ("pan", "tilt", "zoom"):
            v = d.get(axis)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0:
                setattr(self, f"{axis}_scale", min(SCALE_MAX, max(SCALE_MIN, float(v))))

    def latency_for(self, stream_kind: str | None) -> float:
        """Задержка видео для вида потока (mjpeg/h264/sim), по умолчанию — DEFAULT_VIDEO_LATENCY_S."""
        return self.video_latency.get(stream_kind or "", DEFAULT_VIDEO_LATENCY_S)

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
            # замеры поверх типовых: у старой калибровки нет новых видов потока (mjpeg_2)
            video_latency={**he42.VIDEO_LATENCY_S,
                           **{k: float(v) for k, v in (d.get("video_latency") or {}).items()}},
            created=d.get("created"),
        )


# Ярлык таблицы без калибровки в CameraConfig.speed_scale: поправка, найденная слежением, относится к своей
# таблице. Без ярлыка — к прежней (одна точка по зуму, до 2026-10-29), к нынешней она не годится.
DEFAULT_TABLE_ID = "he42-deg-s-2026-10-29"

# Таблица без калибровки — точки по зуму через столько шагов #GZ: угол обзора меняется геометрически, а таблица
# между точками линейна; при 16 отрезках (×1,2 на отрезок) ошибка середины отрезка ≤ 0,5%.
DEFAULT_ZOOM_STEPS = 16


def default_calibration(safety: float = 1.0) -> Calibration:
    """Средние значения до калибровки: камера держит постоянные °/с (he42.PAN_DEG_S) на любом зуме, поэтому
    в долях кадра скорость растёт при приближении как 1 / угол обзора; точки — DEFAULT_ZOOM_STEPS + 1 положений
    зума. Наклон — по той же таблице (у HE42 он в °/с ровно как поворот). Было (до 2026-10-29): одна точка
    по зуму — одни и те же fw/s на любом зуме (думали, камера сама замедляет pan на теле).

    safety > 1 — считать камеру быстрее замеров: ошибиться в «медленную» сторону безопасно
    (регулятор устойчив до ρ≈0.5), а в «быструю» — раскачка (устойчив до ρ≈1.7).
    """
    span = P.LENS_MAX - P.LENS_MIN
    points = {}
    for k in range(DEFAULT_ZOOM_STEPS + 1):
        z = P.LENS_MIN + round(span * k / DEFAULT_ZOOM_STEPS)
        hfov = he42.hfov_deg((z - P.LENS_MIN) / span)
        points[z] = [(c, deg * safety / hfov) for c, deg in he42.PAN_DEG_S.items()]
    return Calibration(pan=SpeedTable(points), zoom=ZoomTable(list(he42.ZOOM_LN_S.items())))
