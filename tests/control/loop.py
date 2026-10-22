"""Виртуальный замкнутый контур для тестов регулятора: «мир» → кадр с задержкой → регулятор → камера."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ptztracker.camera import he42
from ptztracker.camera import protocol as P
from ptztracker.control.camera_model import CameraDynamics, default_calibration
from ptztracker.control.composition import COMPOSITIONS, BodyMeasure
from ptztracker.control.effort import EffortProfile
from ptztracker.control.framing import FramingController, FramingInput

CTRL_DT = 0.04  # 25 Гц
MEAS_DT = 1 / 14  # анализ 14 к/с
APC_PER_FW = 8000  # единиц #APC на ширину кадра при зуме старта (как у симулятора на широком угле)


@dataclass
class Trace:
    t: list = field(default_factory=list)
    pan: list = field(default_factory=list)
    tilt: list = field(default_factory=list)
    zoom: list = field(default_factory=list)
    img_x: list = field(default_factory=list)  # истинное положение носа в кадре «сейчас»
    img_y: list = field(default_factory=list)
    u: list = field(default_factory=list)
    state: list = field(default_factory=list)
    cam_zoom: list = field(default_factory=list)  # ln масштаба камеры (правда)
    model_zoom: list = field(default_factory=list)  # ln масштаба в модели регулятора


def run(
    world_x,
    world_y=lambda t: 0.25,
    world_u=lambda t: 0.19,
    T: float = 10.0,
    latency: float = 0.15,
    ctrl_latency: float | None = None,
    effort: float = 0.5,
    comp: str = "waist_center",
    visible=lambda t: True,
    speed_factor: float = 1.0,  # реальная камера быстрее модели в столько раз (рассогласование калибровки)
    cmd_delay: float = 0.0,  # команды доходят до камеры позже (очередь планировщика)
    zoom_cmd_delay: float | None = None,  # зум ждёт в очереди отдельно (за поворотом); None — как cmd_delay
    zoom_speed_factor: float = 1.0,  # зум камеры быстрее таблицы калибровки в столько раз
    gz_period: float | None = None,  # опрос #GZ: положение зума уходит регулятору (None — не опрашивать)
    apc_period: float | None = None,  # опрос #APC: положение pan/tilt уходит регулятору (None — не опрашивать)
    zoom_pos: int = 0x73F,
    controller_hook=None,
) -> Trace:
    calib = default_calibration()
    cam_x = CameraDynamics(calib.dead_time, calib.tau)
    cam_y = CameraDynamics(calib.dead_time, calib.tau)
    cam_z = CameraDynamics(calib.dead_time, calib.tau)
    ctrl = FramingController(
        COMPOSITIONS[comp], EffortProfile.at(effort), calib, latency if ctrl_latency is None else ctrl_latency
    )
    tr = Trace()
    t, next_meas = 0.0, 0.0
    pending = []  # (когда придёт, t_shot, measure)
    queued = []  # (когда дойдёт до камеры, pan, tilt)
    zqueued = []  # (когда дойдёт до камеры, zoom)
    zdelay = cmd_delay if zoom_cmd_delay is None else zoom_cmd_delay
    gz_pos, gz_t, next_gz = zoom_pos, None, 0.0
    apc, apc_t, next_apc = None, None, 0.0
    while t < T:
        if t >= next_meas:
            next_meas += MEAS_DT
            if visible(t):
                zx = math.exp(cam_z.position(t))
                ix = 0.5 + (world_x(t) - cam_x.position(t) - 0.5) * zx
                iy = 0.5 + (world_y(t) + cam_y.position(t) - 0.5) * zx
                pending.append((t + latency, BodyMeasure((ix, iy), world_u(t) * zx, True)))
            else:
                pending.append((t + latency, None))
        measure, t_cap = None, None
        while pending and pending[0][0] <= t:
            t_cap, measure = pending.pop(0)
        if gz_period is not None and t >= next_gz - 1e-9:
            next_gz += gz_period
            gz_t = t
            gz_pos = round(zoom_pos + cam_z.position(t) / math.log(he42.ZOOM_RATIO) * (P.LENS_MAX - P.LENS_MIN))
        if apc_period is not None and t >= next_apc - 1e-9:
            next_apc += apc_period
            apc_t = t
            apc = (round(0x8000 + cam_x.position(t) * APC_PER_FW), round(0x8000 + cam_y.position(t) * APC_PER_FW))
        out = ctrl.step(FramingInput(now=t, t_capture=t_cap, measure=measure, zoom_pos=gz_pos, zoom_pos_t=gz_t,
                                     pan_tilt=apc, pan_tilt_t=apc_t))
        if controller_hook:
            controller_hook(t, ctrl, out)
        queued.append((t + cmd_delay, out.pan, out.tilt))
        zqueued.append((t + zdelay, out.zoom))
        while queued and queued[0][0] <= t + 1e-9:
            _, qp, qt = queued.pop(0)
            k = math.exp(cam_z.position(t))
            cam_x.command(t, speed_factor * qp / k)  # камера даёт fw/s при текущем зуме → мировые единицы
            cam_y.command(t, speed_factor * qt / k)
        while zqueued and zqueued[0][0] <= t + 1e-9:
            _, qz = zqueued.pop(0)
            cam_z.command(t, zoom_speed_factor * qz)
            ctrl.zoom_sent(t, qz)  # как Follower по on_sent планировщика: модель знает, что ушло в камеру
        zx = math.exp(cam_z.position(t))
        tr.t.append(t)
        tr.pan.append(out.pan)
        tr.tilt.append(out.tilt)
        tr.zoom.append(out.zoom)
        tr.img_x.append(0.5 + (world_x(t) - cam_x.position(t) - 0.5) * zx)
        tr.img_y.append(0.5 + (world_y(t) + cam_y.position(t) - 0.5) * zx)
        tr.u.append(world_u(t) * zx)
        tr.state.append(out.state)
        tr.cam_zoom.append(cam_z.position(t))
        tr.model_zoom.append(ctrl.zdyn.position(t))
        t = round(t + CTRL_DT, 6)
    return tr


def sign_changes(values, eps=1e-6):
    s = [1 if v > eps else -1 for v in values if abs(v) > eps]
    return sum(1 for a, b in zip(s, s[1:]) if a != b)
