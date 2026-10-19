"""Виртуальный замкнутый контур для тестов регулятора: «мир» → кадр с задержкой → регулятор → камера."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ptztracker.control.camera_model import CameraDynamics, default_calibration
from ptztracker.control.composition import COMPOSITIONS, BodyMeasure
from ptztracker.control.effort import EffortProfile
from ptztracker.control.framing import FramingController, FramingInput

CTRL_DT = 0.04  # 25 Гц
MEAS_DT = 1 / 14  # анализ 14 к/с


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
    queued = []  # (когда дойдёт до камеры, pan, tilt, zoom)
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
        out = ctrl.step(FramingInput(now=t, t_capture=t_cap, measure=measure, zoom_pos=zoom_pos))
        if controller_hook:
            controller_hook(t, ctrl, out)
        queued.append((t + cmd_delay, out.pan, out.tilt, out.zoom))
        while queued and queued[0][0] <= t + 1e-9:
            _, qp, qt, qz = queued.pop(0)
            k = math.exp(cam_z.position(t))
            cam_x.command(t, speed_factor * qp / k)  # камера даёт fw/s при текущем зуме → мировые единицы
            cam_y.command(t, speed_factor * qt / k)
            cam_z.command(t, qz)
        zx = math.exp(cam_z.position(t))
        tr.t.append(t)
        tr.pan.append(out.pan)
        tr.tilt.append(out.tilt)
        tr.zoom.append(out.zoom)
        tr.img_x.append(0.5 + (world_x(t) - cam_x.position(t) - 0.5) * zx)
        tr.img_y.append(0.5 + (world_y(t) + cam_y.position(t) - 0.5) * zx)
        tr.u.append(world_u(t) * zx)
        tr.state.append(out.state)
        t = round(t + CTRL_DT, 6)
    return tr


def sign_changes(values, eps=1e-6):
    s = [1 if v > eps else -1 for v in values if abs(v) > eps]
    return sum(1 for a, b in zip(s, s[1:]) if a != b)
