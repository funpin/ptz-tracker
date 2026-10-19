"""Регулятор кадрирования: по положению человека решает, с какой скоростью вести pan/tilt/zoom.

Чистая функция шага (без потоков и сети), вызывается ~25 раз в секунду. Как оператор:
- не реагирует на покачивания внутри мёртвой зоны;
- трогается и тормозит плавно (ограничение ускорения поверх разгона самой камеры);
- идёт вместе с человеком (скорость человека «в мире» — упреждение);
- учитывает, что кадр опаздывает: камера уже сдвинула картинку, пока кадр шёл по сети;
- оставляет место перед идущим человеком;
- зумирует только когда кадр спокоен, а отъезжает сразу, если человек у края.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from ptztracker.control.camera_model import Calibration, CameraDynamics
from ptztracker.control.composition import BodyMeasure, Composition
from ptztracker.control.effort import EffortProfile

ALPHA = 0.35  # сглаживание положения человека
VEL_WINDOW_S = 0.4  # окно оценки скорости человека
STILL_V = 0.02  # медленнее — считаем, что человек стоит
START_V, STOP_V = 0.05, 0.03  # скорость человека для перехода в «движение» / обратно в «стоим»
RELEASE = 0.4  # возврат в «стоим», когда ошибка < RELEASE·dead
EDGE_GAIN = 1.8  # за мягкой зоной догоняем сильнее
LEAD_MAX, LEAD_TAU = 0.08, 0.8
SETTLE_V = 0.06  # человек остановился, камера проехала — возвращаем медленно, без «пинг-понга»
NO_TARGET_S = 0.3
ZOOM_ACCEL = 0.5  # ln/с²
ZOOM_IN_AFTER_HOLD_S = 1.0
U_SMOOTH = 0.3


@dataclass(frozen=True)
class FramingInput:
    now: float
    t_capture: float | None  # когда пришёл кадр, по которому измерено (None — нового замера нет)
    measure: BodyMeasure | None
    zoom_pos: int
    aspect: float = 16 / 9


@dataclass(frozen=True)
class FramingOutput:
    pan: float  # fw/s, > 0 — вправо
    tilt: float  # fh/s, > 0 — вверх
    zoom: float  # ln/s, > 0 — к теле
    state: Literal["idle", "holding", "moving"]
    error: tuple[float, float]
    size_error: float | None


def _limit(v: float, lim: float) -> float:
    return max(-lim, min(lim, v))


class _Axis:
    """Одна ось (pan по x или tilt по y). sign: +1 — движение камеры уводит картинку к меньшим координатам.

    Внутри всё считается в «мировых» единицах, не зависящих от зума: смещение от центра кадра,
    делённое на текущий масштаб зума k. Иначе наезд (лицо уходит от центра) выглядел бы как ходьба.
    """

    def __init__(self, sign: int, dyn: CameraDynamics):
        self.sign = sign
        self.dyn = dyn
        self.reset()

    def reset(self) -> None:
        self.world: float | None = None
        self.v_world = 0.0  # мировые единицы/с
        self.v_img = 0.0  # то же в долях кадра/с при текущем зуме
        self.hist: list[tuple[float, float]] = []
        self.t_meas: float | None = None
        self.mode = "holding"
        self.v_cmd = 0.0
        self.lead = 0.0
        self.err = 0.0

    def _cam(self, t: float) -> float:
        return self.dyn.displacement(0.0, t)

    def measure(self, img: float, t_shot: float, k_shot: float) -> None:
        # «мировая» позиция: что было бы без поворота камеры и без зума
        z = (img - 0.5) / k_shot + self.sign * self._cam(t_shot)
        if self.world is None or self.t_meas is None:
            self.world, self.v_world, self.t_meas = z, 0.0, t_shot
            self.hist = [(t_shot, z)]
            return
        dt = max(1e-3, t_shot - self.t_meas)
        pred = self.world + self.v_world * dt
        self.world = pred + ALPHA * (z - pred)
        self.t_meas = t_shot
        # скорость — наклон по последним VEL_WINDOW_S: быстро замечает, что человек остановился
        self.hist.append((t_shot, z))
        self.hist = [(t, v) for t, v in self.hist if t_shot - t <= VEL_WINDOW_S]
        if len(self.hist) >= 3:
            ts = [t for t, _ in self.hist]
            zs = [v for _, v in self.hist]
            mt, mz = sum(ts) / len(ts), sum(zs) / len(zs)
            den = sum((t - mt) ** 2 for t in ts)
            if den > 0:
                self.v_world = sum((t - mt) * (v - mz) for t, v in zip(ts, zs)) / den

    def step(
        self,
        now: float,
        dt: float,
        anchor: float,
        dead: float,
        soft: float,
        e: EffortProfile,
        lead_k: float,
        active: bool,
        k_now: float = 1.0,
    ) -> float:
        if active and self.world is not None:
            self.v_img = self.v_world * k_now
            v_w = self.v_img if abs(self.v_img) >= STILL_V else 0.0
            img_now = 0.5 + (self.world + self.v_world * (now - self.t_meas) - self.sign * self._cam(now)) * k_now
            lead_target = _limit(-lead_k * v_w, LEAD_MAX)
            self.lead += (lead_target - self.lead) * (1 - math.exp(-dt / LEAD_TAU))
            self.err = img_now - (anchor + self.lead)
            a = abs(self.err)
            if self.mode == "holding" and (a > dead or abs(v_w) >= START_V):
                self.mode = "moving"
            elif self.mode == "moving" and a < RELEASE * dead and abs(v_w) < STOP_V:
                self.mode = "holding"
            if self.mode == "moving":
                base = RELEASE * dead
                shaped = max(0.0, a - base)
                if a > soft:
                    shaped = (soft - base) + EDGE_GAIN * (a - soft)
                want = self.sign * (v_w + e.gain * math.copysign(shaped, self.err))
                # Камера проехала остановившегося (или замедлившегося) человека: назад — только медленно,
                # пока он в мягкой зоне. Оператор так и делает: доводит, а не дёргает обратно.
                against = want * self.sign * self.v_img < 0
                if a <= soft and (abs(self.v_img) < STOP_V or against):
                    want = _limit(want, SETTLE_V)
            else:
                want = 0.0
        else:
            want = 0.0
        want = _limit(want, e.v_max)
        self.v_cmd += _limit(want - self.v_cmd, e.a_max * dt)
        if abs(self.v_cmd) < 1e-9:
            self.v_cmd = 0.0
        self.dyn.command(now, self.v_cmd / k_now)  # камера даёт fw/s при текущем зуме → в мировые единицы
        return self.v_cmd


class FramingController:
    def __init__(self, composition: Composition, effort: EffortProfile, calib: Calibration, video_latency: float):
        self.composition = composition
        self.effort = effort
        self.calib = calib
        self.video_latency = video_latency
        self.reset()

    def set_composition(self, c: Composition) -> None:
        self.composition = c

    def set_effort(self, e: EffortProfile) -> None:
        self.effort = e

    def reset(self) -> None:
        c = self.calib
        self.x = _Axis(+1, CameraDynamics(c.dead_time, c.tau))
        self.y = _Axis(-1, CameraDynamics(c.dead_time, c.tau))
        self.zdyn = CameraDynamics(c.dead_time, c.tau)
        self._t_prev: float | None = None
        self._t_seen: float | None = None
        self._last_cap: float | None = None
        self._u: float | None = None
        self._u_t: float | None = None
        self._zoom_cmd = 0.0
        self._zoom_active = False
        self._hold_since: float | None = None
        self._still_since: float | None = None

    def step(self, inp: FramingInput) -> FramingOutput:
        now = inp.now
        dt = CTRL_DT_DEFAULT if self._t_prev is None else max(1e-3, now - self._t_prev)
        self._t_prev = now
        e, comp = self.effort, self.composition

        if inp.measure is not None and inp.t_capture is not None and inp.t_capture != self._last_cap:
            self._last_cap = inp.t_capture
            self._t_seen = now
            t_shot = inp.t_capture - self.video_latency
            k_shot = self._k(t_shot)
            self.x.measure(inp.measure.nose[0], t_shot, k_shot)
            self.y.measure(inp.measure.nose[1], t_shot, k_shot)
            if inp.measure.u is not None:
                # размер «сейчас»: за время в пути кадра зум мог измениться
                u_now = inp.measure.u * math.exp(self.zdyn.displacement(t_shot, now))
                self._u = u_now if self._u is None else self._u + U_SMOOTH * (u_now - self._u)
                self._u_t = now

        active = self._t_seen is not None and now - self._t_seen <= NO_TARGET_S
        dead_y = e.dead_y * inp.aspect / (16 / 9)
        k_now = self._k(now)
        pan = self.x.step(now, dt, comp.anchor[0], e.dead_x, e.soft_x, e, e.lead, active, k_now)
        tilt = self.y.step(now, dt, comp.anchor[1], dead_y, e.soft_y, e, 0.0, active, k_now)

        if not active:
            state = "idle"
            self.x.mode = self.y.mode = "holding"
            self._hold_since = None
        elif self.x.mode == "moving" or self.y.mode == "moving":
            state = "moving"
            self._hold_since = None
        else:
            state = "holding"
            if self._hold_since is None:
                self._hold_since = now

        still = active and abs(self.x.v_img) < STOP_V and abs(self.y.v_img) < STOP_V
        if not still:
            self._still_since = None
        elif self._still_since is None:
            self._still_since = now
        zoom, size_err = self._zoom(now, dt, active, state, e)
        return FramingOutput(pan, tilt, zoom, state, (self.x.err, self.y.err), size_err)

    def _k(self, t: float) -> float:
        """Во сколько раз картинка увеличена зумом с момента старта регулятора."""
        return math.exp(self.zdyn.displacement(0.0, t))

    def _zoom(self, now: float, dt: float, active: bool, state: str, e: EffortProfile) -> tuple[float, float | None]:
        want = 0.0
        size_err = None
        if active:
            at_edge = abs(self.x.err) > e.soft_x or abs(self.y.err) > e.soft_y
            if self._u is not None and self._u > 0:
                size_err = math.log(self._u / self.composition.u_frac)
                a = abs(size_err)
                if not self._zoom_active and a > e.zoom_dead:
                    self._zoom_active = True
                elif self._zoom_active and a < 0.5 * e.zoom_dead:
                    self._zoom_active = False
                if self._zoom_active:
                    want = _limit(-e.zoom_gain * size_err, e.zoom_max)
                    # Наезд — только когда человек сам стоит (≥1 с). Камера при этом может подправлять
                    # наклон: наезд идёт одним плавным движением, а не «ступеньками».
                    calm = self._still_since is not None and now - self._still_since >= ZOOM_IN_AFTER_HOLD_S
                    if want > 0 and not calm:
                        want = 0.0
            if at_edge:
                want = -e.zoom_max  # человек уходит из кадра — сразу шире
        self._zoom_cmd += _limit(want - self._zoom_cmd, ZOOM_ACCEL * dt)
        if abs(self._zoom_cmd) < 1e-9:
            self._zoom_cmd = 0.0
        self.zdyn.command(now, self._zoom_cmd)
        return self._zoom_cmd, size_err


CTRL_DT_DEFAULT = 0.04
