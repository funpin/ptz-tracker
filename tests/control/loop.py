"""Виртуальный замкнутый контур для тестов регулятора: «мир» → кадр с задержкой → регулятор → камера."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ptztracker.camera import he42
from ptztracker.camera import protocol as P
from ptztracker.control.calibration import hfov_at
from ptztracker.control.camera_model import CameraDynamics, default_calibration
from ptztracker.control.composition import COMPOSITIONS, BodyMeasure
from ptztracker.control.effort import EffortProfile
from ptztracker.control.follower import output_codes
from ptztracker.control.framing import PT_QUEUE_S, TILT_FROM_FH, FramingController, FramingInput
from ptztracker.control.speed_adapt import SpeedAdapter
from ptztracker.sim.ptz_model import he42_deg_s

CTRL_DT = 0.04  # 25 Гц
MEAS_DT = 1 / 14  # анализ 14 к/с
# Наибольшая очередь команд поворота, которую проверяют тесты: p95 живого пути решение → камера 121–131 мс
# (2026-10-29). Модель регулятора ждёт половину (framing.PT_QUEUE_S) — устойчивость при ρ 0,5–1,4 держит запас.
QUEUE_MAX = 0.13
# Только крайний случай: таблица неверна в ×2 и подстройка по #APC ещё не нашла поправку — за первое стояние
# при 0,13 с два разворота (не раскачка: сторож молчит), при 0,08–0,10 — один (DECISIONS, задача 6).
QUEUE_X2_BEFORE_ADAPT = 0.08


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
    scale: list = field(default_factory=list)  # поправка скорости (pan, tilt, zoom) после такта (adapt=True)


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
    # команды доходят до камеры позже (очередь планировщика); по умолчанию — столько, сколько ждёт модель регулятора
    cmd_delay: float = PT_QUEUE_S,
    zoom_cmd_delay: float | None = None,  # зум ждёт в очереди отдельно (за поворотом); None — как cmd_delay
    zoom_speed_factor: float = 1.0,  # зум камеры быстрее таблицы калибровки в столько раз
    gz_period: float | None = None,  # опрос #GZ: положение зума уходит регулятору (None — не опрашивать)
    apc_period: float | None = None,  # опрос #APC: положение pan/tilt уходит регулятору (None — не опрашивать)
    zoom_pos: int = 0x73F,
    controller_hook=None,
    cam_tau: float | None = None,  # разгон камеры на самом деле (None — как в калибровке)
    cam_dead_time: float | None = None,  # мёртвое время камеры на самом деле (None — как в калибровке)
    meas_dt: float = MEAS_DT,  # период замеров анализа (MJPEG 2: 12 к/с → 1/12)
    adapt: bool = False,  # как Follower: скорости → коды камеры (с поправкой), подстройка скорости по #APC/#GZ
    # камера — как живая HE42 (adapt=True): постоянные °/с по коду (sim.ptz_model.he42_deg_s) до 0xD54, выше — медленнее,
    # а не таблица калибровки регулятора: проверяет саму таблицу (площадка 2026-10-29, §3б)
    cam_he42: bool = False,
    # наклон камеры ÷ поворот на одном коде (у HE42 — 1); measured_tilt — мастер его измерил (Calibration.tilt)
    tilt_factor: float = 1.0,
    measured_tilt: bool = False,
) -> Trace:
    calib = default_calibration()
    if measured_tilt:
        calib.tilt = [(63, tilt_factor), (75, tilt_factor)]
    adapter = SpeedAdapter(calib) if adapt else None
    codes = (50, 50, 50)
    # #APC: положение камеры в градусах (единицы — he42.APC_DEG_PER_UNIT); мировые единицы — доли кадра
    # при зуме старта (pan) и доли его высоты (tilt)
    apc_per_fw = hfov_at(zoom_pos) / he42.APC_DEG_PER_UNIT
    cam_dyn = (calib.dead_time if cam_dead_time is None else cam_dead_time, calib.tau if cam_tau is None else cam_tau)
    cam_x, cam_y, cam_z = CameraDynamics(*cam_dyn), CameraDynamics(*cam_dyn), CameraDynamics(*cam_dyn)
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
            next_meas += meas_dt
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
            apc = (round(0x8000 + cam_x.position(t) * apc_per_fw),
                   round(0x8000 + cam_y.position(t) * TILT_FROM_FH * apc_per_fw))
        out = ctrl.step(FramingInput(now=t, t_capture=t_cap, measure=measure, zoom_pos=gz_pos, zoom_pos_t=gz_t,
                                     pan_tilt=apc, pan_tilt_t=apc_t))
        if controller_hook:
            controller_hook(t, ctrl, out)
        if adapter is not None:  # как Follower._tick: замеры положения → поправка → коды
            if gz_t == t:
                adapter.zoom_measured(t, gz_pos, frozen=ctrl.diverged)
            if apc_t == t:
                adapter.pan_tilt_measured(t, apc[0], apc[1], gz_pos, frozen=ctrl.diverged)
            codes = output_codes(calib, out, gz_pos, codes)
            queued.append((t + cmd_delay, codes[0], codes[1]))
            zqueued.append((t + zdelay, codes[2]))
        else:
            queued.append((t + cmd_delay, out.pan, out.tilt))
            zqueued.append((t + zdelay, out.zoom))
        while queued and queued[0][0] <= t + 1e-9:
            _, qp, qt = queued.pop(0)
            k = math.exp(cam_z.position(t))
            if adapter is not None and cam_he42:  # °/с камеры → мировые единицы (доли кадра на зуме старта)
                adapter.pt_sent(t, qp, qt, gz_pos)
                true_gz = round(zoom_pos + cam_z.position(t) / math.log(he42.ZOOM_RATIO) * (P.LENS_MAX - P.LENS_MIN))
                cam_x.command(t, speed_factor * he42_deg_s(qp, true_gz) / hfov_at(zoom_pos))  # на теле — медленнее
                cam_y.command(t, speed_factor * tilt_factor * he42_deg_s(qt, true_gz) / hfov_at(zoom_pos) / TILT_FROM_FH)
                continue
            if adapter is not None:  # камера едет по таблице без поправки, только в speed_factor раз быстрее
                adapter.pt_sent(t, qp, qt, gz_pos)
                qp, qt = calib.pan.speed(qp, gz_pos), tilt_factor * calib.pan.speed(qt, gz_pos) / TILT_FROM_FH
            cam_x.command(t, speed_factor * qp / k)  # камера даёт fw/s при текущем зуме → мировые единицы
            cam_y.command(t, speed_factor * qt / k)
        while zqueued and zqueued[0][0] <= t + 1e-9:
            _, qz = zqueued.pop(0)
            if adapter is not None:
                adapter.zoom_sent(t, qz)
                cam_z.command(t, zoom_speed_factor * calib.zoom.rate(qz))
                ctrl.zoom_sent(t, calib.zoom_rate(qz))  # модель — по таблице с поправкой
                continue
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
        tr.scale.append((calib.pan_scale, calib.tilt_scale, calib.zoom_scale))
        t = round(t + CTRL_DT, 6)
    return tr


def sign_changes(values, eps=1e-6):
    s = [1 if v > eps else -1 for v in values if abs(v) > eps]
    return sum(1 for a, b in zip(s, s[1:]) if a != b)
