import gzip
import json
import math
from pathlib import Path

from loop import CTRL_DT, QUEUE_MAX, run, sign_changes

from ptztracker.camera import he42
from ptztracker.control.camera_model import CameraDynamics, default_calibration
from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.control.framing import (
    BRAKE,
    TRACK_ZOOM_MAX,
    WATCHDOG_MOVING_V,
    ZOOM_LIMIT_LEAD_S,
    ZOOM_LIMIT_RELEASE,
    FramingController,
    SwingWatch,
)

AX = COMPOSITIONS["waist_center"].anchor[0]
E = EffortProfile.at(0.5)


def test_holds_still_when_inside_dead_zone():
    tr = run(lambda t: AX + 0.03, T=5)
    assert all(p == 0 for p in tr.pan)


def _camera_motion(tr):
    """Скорость, ускорение и рывок камеры по командам стенда (модель разгона калибровки, шаг 0,01 с)."""
    c = default_calibration()
    cam = CameraDynamics(c.dead_time, c.tau)
    for t, p in zip(tr.t, tr.pan):
        cam.command(t, p)
    ts = [i * 0.01 for i in range(round(tr.t[-1] / 0.01))]
    v = [cam.velocity(t) for t in ts]
    acc = [(b - a) / 0.01 for a, b in zip(v, v[1:])]
    jerk = [(b - a) / CTRL_DT for a, b in zip(acc[::4], acc[4::4])]
    return v, acc, jerk


def test_starts_smoothly_and_accel_limited():
    """Плавность — у движения камеры: её ускорение (модель разгона) не больше a_max, рывок — не больше j_max.
    Команда может опережать камеру на a_max·τ: камера сама разгоняется с τ (часть 1, задача 4)."""
    for eff in (0.0, 0.5):
        e = EffortProfile.at(eff)
        tr = run(lambda t: AX + 0.15, T=4, effort=eff)
        _, acc, jerk = _camera_motion(tr)
        assert max(map(abs, acc)) <= e.a_max * 1.02, eff
        assert max(map(abs, jerk)) <= e.j_max * 1.02, eff
        assert next(p for p in tr.pan if p != 0) > 0  # человек справа → камера вправо


def test_feedforward_matches_walking_speed():
    tr = run(lambda t: AX + 0.2 * t, T=6)
    late = [i for i, t in enumerate(tr.t) if t > 4]
    assert all(abs(tr.pan[i] - 0.2) < 0.05 for i in late)
    assert all(abs(tr.img_x[i] - AX) < E.soft_x for i in late)


def walk_then_stop(t, speed=0.25, t_stop=3.0):
    return AX + speed * min(t, t_stop)


def test_stops_without_overshoot_after_walk():
    # Тормозной путь камеры (очередь + мёртвое время + τ 0,12 с + задержка кадра) физически даёт перелёт ~v·0,5.
    # Важно другое: человек не уходит из мягкой зоны, назад камера возвращается медленно
    # (без «пинг-понга»), разворот не больше одного, и в конце всё спокойно.
    from ptztracker.control.framing import SETTLE_V

    for speed in (0.1, 0.25):
        tr = run(lambda t, speed=speed: walk_then_stop(t, speed), T=13)
        after = [i for i, t in enumerate(tr.t) if t > 3.0]
        assert max(abs(tr.img_x[i] - AX) for i in after) <= E.soft_x
        # медленный возврат; сверху допускается доворот, компенсирующий отъезд зума у края кадра
        assert min(tr.pan[i] for i in after) >= -SETTLE_V * 1.5
        assert sign_changes(tr.pan) <= 1
        assert tr.pan[-1] == 0 and abs(tr.img_x[-1] - AX) < E.dead_x
    slow = run(lambda t: walk_then_stop(t, 0.1), T=13)
    assert max(AX - x for x, t in zip(slow.img_x, slow.t) if t > 3.0) <= E.dead_x * 1.5  # медленно — почти без перелёта


def test_latency_compensation_reduces_overshoot():
    """Компенсация задержки видео (0,35 с): на ходу человек ближе к якорю, после остановки перелёт меньше,
    разворотов меньше. Ходьба 8 с — упреждение успевает выйти на скорость человека (на 3 с ходьбы с места
    перелёт задаёт догон, а не компенсация)."""
    for speed in (0.25, 0.35):
        res = {}
        for ctrl_latency in (0.35, 0.0):
            tr = run(lambda t, s=speed: walk_then_stop(t, s, 8.0), T=14, latency=0.35, ctrl_latency=ctrl_latency)
            walk = [abs(x - AX) for x, t in zip(tr.img_x, tr.t) if 1.5 < t < 8.0]
            res[ctrl_latency] = (sum(walk) / len(walk), max(AX - x for x, t in zip(tr.img_x, tr.t) if t > 8.0),
                                 sign_changes(tr.pan, WATCHDOG_MOVING_V))
        (err_c, over_c, rev_c), (err_0, over_0, rev_0) = res[0.35], res[0.0]
        # перелёт после остановки физически ≈ v·(видео 0,35 + очередь PT_QUEUE_S + мёртвое время + τ + два кадра
        # на «остановился») ≈ 0,75·v; замер с компенсацией 0,79·v / 0,83·v (0,20 / 0,29), без неё 0,29 / 0,40
        assert err_c < 0.5 * err_0 and over_c < over_0 and over_c <= 0.85 * speed and rev_c < rev_0, (speed, res)


def test_no_target_ramps_to_zero():
    tr = run(lambda t: AX + 0.3 * t, visible=lambda t: t < 3, T=7)
    assert tr.pan[-1] == 0 and tr.state[-1] == "idle"
    # без цели (последний кадр — 3,0 + задержка): торможение как у камеры, BRAKE·a_max; пока цель видна,
    # команда может опережать камеру на a_max·τ — плавность там проверяет test_starts_smoothly_and_accel_limited
    lost = [p for p, t in zip(tr.pan, tr.t) if t >= 3.15]
    steps = [abs(b - a) for a, b in zip(lost, lost[1:])]
    assert max(steps) <= BRAKE * E.a_max * CTRL_DT + 1e-9


def test_zoom_in_only_when_holding_and_zoom_out_at_edge():
    # человек мелкий (нужен наезд) и идёт → наезд не начинается, пока камера едет
    tr = run(lambda t: AX + 0.2 * min(t, 3), world_u=lambda t: 0.08, T=16)
    walking = [i for i, t in enumerate(tr.t) if t < 3.3]
    assert all(tr.zoom[i] <= 0 for i in walking)  # пока идёт — не наезжаем
    assert max(tr.zoom) > 0  # остановился — наезд
    pulses = sum(1 for a, b in zip(tr.zoom, tr.zoom[1:]) if a <= 0 < b)
    assert pulses == 1  # одним плавным движением, без «ступенек»
    assert abs(tr.u[-1] / 0.19 - 1) < 0.15
    # человек у края кадра → отъезд сразу, даже если размер в норме
    tr2 = run(lambda t: AX + 0.45, T=1.5)
    assert min(tr2.zoom) < 0


def _gz_of(tr, zoom_pos):
    """Положение #GZ камеры по ln масштаба в следе замкнутого контура (старт — zoom_pos)."""
    from ptztracker.camera import protocol as P

    return [zoom_pos + z / math.log(he42.ZOOM_RATIO) * (P.LENS_MAX - P.LENS_MIN) for z in tr.cam_zoom]


def test_zoom_in_stops_at_track_zoom_max_for_far_person():
    """HE40 2026-10-30: «Пояс» на дальнего человека (u ≈ 0,02) увёл зум до 0xF41 (~3°). Слежение не приближает
    дальше TRACK_ZOOM_MAX: зум останавливается у предела (с допуском на разгон/торможение), команд «ближе» дальше
    нет, туда-сюда не качается."""
    for adapt in (False, True):
        tr = run(lambda t: AX, world_u=lambda t: 0.02, T=45, zoom_pos=0x73F, gz_period=0.5,
                 apc_period=0.5 if adapt else None, adapt=adapt, cmd_delay=QUEUE_MAX)
        gz = _gz_of(tr, 0x73F)
        assert max(gz) <= TRACK_ZOOM_MAX + 0x20, (adapt, hex(round(max(gz))))
        assert max(gz) >= TRACK_ZOOM_MAX - 0x60, (adapt, hex(round(max(gz))))  # дошла до предела, не застряла раньше
        assert sign_changes(tr.zoom) == 0, adapt  # без «туда-сюда»
        stop = next(i for i, g in enumerate(gz) if g >= TRACK_ZOOM_MAX - 0x20)
        assert all(z <= 0 for z in tr.zoom[stop + 100:]), adapt  # у предела наезда больше нет
        assert gz[-1] - gz[stop + 100] <= 0x10, adapt


def test_zoom_command_cut_at_limit_even_when_size_error_is_in_dead_zone():
    """У предела идущий наезд гасится сразу, а не плавным спадом: размер цели уже в мёртвой зоне (want = 0),
    но команда «ближе» ещё >0 — раньше её гасил только случай want > 0, и зум полз за предел."""
    for eff in (0.0, 0.5, 1.0):
        e = EffortProfile.at(eff)
        ctrl = FramingController(COMPOSITIONS["waist_center"], e, default_calibration(), 0.15)
        ctrl._u = ctrl.composition.u_frac  # размер ровно по цели: ошибка 0, в мёртвой зоне
        ctrl._zoom_cmd = e.zoom_max  # наезд идёт на полной скорости
        cmd, size_err = ctrl._zoom(1.0, 0.04, True, False, e, TRACK_ZOOM_MAX)
        assert abs(size_err) < e.zoom_dead
        assert cmd <= 0.0, eff


def test_zoom_limit_release_exceeds_lead_term_at_sharpest_effort():
    """Обнуление команды наезда при остановке убирает слагаемое ZOOM_LIMIT_LEAD_S·cmd из прогноза: запас снятия
    защёлки должен быть больше него на «Резко», иначе остановка сама снимала бы защёлку и наезд дёргался."""
    assert ZOOM_LIMIT_RELEASE > ZOOM_LIMIT_LEAD_S * EffortProfile.at(1.0).zoom_max


def test_zoom_in_resumes_after_release_far_near_far():
    """Далеко (наезд до предела) → близко (отъезд ниже предела, защёлка снята) → снова далеко: наезд идёт заново
    и снова доходит до предела."""
    far, near = 0.02, 0.5
    tr = run(lambda t: AX, world_u=lambda t: near if 45 <= t < 62 else far, T=110, zoom_pos=0x73F, gz_period=0.5,
             cmd_delay=QUEUE_MAX)
    gz = _gz_of(tr, 0x73F)
    first = [g for t, g in zip(tr.t, gz) if t < 45]
    mid = [g for t, g in zip(tr.t, gz) if 45 <= t < 62]
    last = [g for t, g in zip(tr.t, gz) if t >= 62]
    assert max(first) >= TRACK_ZOOM_MAX - 0x60  # первый подъезд дошёл до предела
    assert min(mid) < TRACK_ZOOM_MAX - 0x100  # отъехал заметно ниже предела (защёлка снята)
    # второй подъезд: зум снова идёт «ближе» и доходит до предела, не выше +0x20
    assert max(last) >= TRACK_ZOOM_MAX - 0x60 and max(last) <= TRACK_ZOOM_MAX + 0x20
    assert max(last) - min(last) >= 0x80


def test_zoom_in_blocked_beyond_limit_but_zoom_out_free():
    """Зум уже выше предела (рука оператора): «ближе» не командуем, отъезд не ограничен."""
    tr = run(lambda t: AX, world_u=lambda t: 0.02, T=10, zoom_pos=0xF00, gz_period=0.5)
    assert all(z <= 0 for z in tr.zoom)
    tr2 = run(lambda t: AX, world_u=lambda t: 0.5, T=12, zoom_pos=0xF00, gz_period=0.5)  # слишком крупный
    assert min(tr2.zoom) < -0.1
    assert _gz_of(tr2, 0xF00)[-1] < 0xE00


def test_zoom_does_not_hunt():
    import math

    tr = run(lambda t: AX, world_u=lambda t: 0.19 * (1 + 0.08 * math.sin(t * 3)), T=10)
    assert all(z == 0 for z in tr.zoom)


def test_tilt_follows_vertical():
    tr = run(lambda t: AX, world_y=lambda t: 0.25 + 0.15 * min(t, 2) / 2, T=7)
    assert abs(tr.img_y[-1] - 0.25) < E.dead_y and sign_changes(tr.tilt) <= 1


# --- устойчивость (финальное ревью этапа 3) -------------------------------------


def _adapt_lead_in(t):
    """20 с подстройки скорости (как test_speed_adapt): ходит 5 с по 0,2 fw/s, стоит 5 с, обратно, стоит."""
    ph = t % 20.0
    x = 0.2 * min(ph, 5.0)
    if ph > 10.0:
        x -= 0.2 * min(ph - 10.0, 5.0)
    return AX + x


def test_walk_at_12_fps_with_mjpeg2_latency():
    """MJPEG (2): кадры 12 к/с (замер каждые 1/12 с) и задержка 0,26 с (he42.VIDEO_LATENCY_S["mjpeg_2"]) —
    человек в кадре, сторож молчит."""
    tr = run(lambda t: AX + 0.25 * min(t, 4.0), T=12, latency=he42.VIDEO_LATENCY_S["mjpeg_2"], meas_dt=1 / 12)
    assert max(abs(x - AX) for x in tr.img_x[int(1.0 / CTRL_DT):]) < 0.35
    assert "diverged" not in tr.state


def test_robust_when_camera_faster_than_model():
    """Калибровка неточна (камера в 1,7 и 2 раза быстрее или вдвое медленнее) + очередь 0,13 с — без раскачки.

    С полным упреждением (FF_GAIN 1,0) устойчивость держит подстройка скорости по #APC (speed_adapt), как
    в Follower: первые 20 с человек ходит и подстройка находит ρ (сторож молчит и тогда), потом — шаг
    0,2 fw/s × 3 с, как в этом тесте до части 1."""
    for rho in (0.5, 1.7, 2.0):
        tr = run(lambda t: _adapt_lead_in(t) if t < 20 else AX + 0.2 * min(max(t - 22, 0.0), 3.0), T=37,
                 speed_factor=rho, cmd_delay=QUEUE_MAX, apc_period=0.5, gz_period=0.5, adapt=True)
        assert "diverged" not in tr.state, rho
        late = [i for i, t in enumerate(tr.t) if t >= 20]
        xs = [tr.img_x[i] for i in late]
        assert 0.3 < min(xs) and max(xs) < (0.75 if rho > 1 else 0.8), rho
        assert sign_changes([tr.pan[i] for i in late]) <= 2, rho


def test_never_sustained_oscillation_either_stable_or_stopped():
    # На эфире раскачка хуже остановки: при любом рассогласовании — либо устойчиво, либо сторож остановил
    for rho, delay in ((2.0, 0.13), (2.5, 0.13), (3.0, 0.0), (4.0, 0.0), (6.0, 0.0)):
        tr = run(lambda t: AX + 0.2 * min(t, 3.0), T=20, speed_factor=rho, cmd_delay=delay)
        if "diverged" in tr.state:
            i = tr.state.index("diverged")
            assert all(p == 0 for p in tr.pan[i + 25 :]), (rho, delay)
        else:
            late = [p for p, t in zip(tr.pan, tr.t) if t > 10]
            assert sign_changes(late) <= 1 and 0.2 < min(tr.img_x) and max(tr.img_x) < 0.8, (rho, delay)


def test_robust_when_camera_slower_than_model():
    tr = run(lambda t: AX + 0.2 * min(t, 3.0), T=15, speed_factor=0.5)
    assert 0.3 < min(tr.img_x) and max(tr.img_x) < 0.8 and sign_changes(tr.pan) <= 2


def test_divergence_watchdog_stops_runaway():
    # Камера в 4 раза быстрее модели (не откалибрована на теле) → раскачка → сторож останавливает
    tr = run(lambda t: AX + 0.2 * min(t, 3.0), T=20, speed_factor=4.0)
    i = next((i for i, s in enumerate(tr.state) if s == "diverged"), None)
    assert i is not None, "сторож не сработал"
    assert all(p == 0 and z == 0 for p, z in zip(tr.pan[i + 25 :], tr.zoom[i + 25 :]))


def test_sway_at_lectern_does_not_hunt():
    for amp, period in ((0.025, 2.5), (0.03, 3.0)):
        tr = run(lambda t, amp=amp, period=period: AX + amp * math.sin(2 * math.pi * t / period), T=15)
        assert sign_changes(tr.pan) <= 2, (amp, period)


def test_target_loss_stops_all_axes_within_1s():
    tr = run(lambda t: AX + 0.3 * t, world_u=lambda t: 0.08, visible=lambda t: t < 3, T=6)
    # последний замер виден в 3.0 + задержка; все оси в ноль не позже чем через 1 с после этого
    after = [i for i, t in enumerate(tr.t) if t >= 3.0 + 0.15 + 1.0]
    assert all(tr.pan[i] == 0 and tr.tilt[i] == 0 and tr.zoom[i] == 0 for i in after)


def test_edge_zoom_out_is_not_counteracted_by_pan():
    # человек у края: отъезд зума сам возвращает его к центру — поворот не должен уводить камеру от него
    tr = run(lambda t: 0.9, T=2.0)
    zoom_out = [i for i, z in enumerate(tr.zoom) if z < 0]
    assert zoom_out and all(tr.pan[i] >= 0 for i in zoom_out)


def test_no_zoom_out_command_beyond_wide_end():
    tr = run(lambda t: 0.9, T=2.0, zoom_pos=0x555)
    assert all(z >= 0 for z in tr.zoom)


# --- модель зума по отправленным командам (часть 1, задача 1) ---------------------


def _zoom_model_error(tr, after=0.0):
    """Наибольшее расхождение масштаба модели и камеры, в долях (0.1 — на 10%)."""
    return max(abs(math.exp(m - c) - 1) for m, c, t in zip(tr.model_zoom, tr.cam_zoom, tr.t) if t >= after)


def _stand_walk_stand(t):
    return AX + 0.2 * min(max(t - 6.0, 0.0), 3.0)


def test_zoom_model_follows_sent_not_decided():
    """Часть 0, B2: зум ждал в очереди до 2,4 с, а модель считала его выполненным (×3,9 при факте ×9,4).

    Зум доходит до камеры через 1,5 с — модель всё равно совпадает с камерой (< 10%).
    """
    tr = run(_stand_walk_stand, world_u=lambda t: 0.08, T=16, cmd_delay=0.065, zoom_cmd_delay=1.5)
    assert max(tr.cam_zoom) > 0.3  # наезд реально был
    assert _zoom_model_error(tr) < 0.10


def test_zoom_model_corrected_by_gz():
    """Таблица зума врёт (камера в 1,5 раза быстрее или в 0,7 раза медленнее): ответы #GZ раз в 0,5 с
    подтягивают модель к камере. Поправка частичная (GZ_GAIN: соответствие #GZ → масштаб живьём не мерялось),
    поэтому во время наезда модель отстаёт, но когда зум стоит ≥ 1,5 с — сходится (< 5%).
    """
    for sf in (1.5, 0.7):
        tr = run(_stand_walk_stand, world_u=lambda t: 0.08, T=20, zoom_speed_factor=sf, gz_period=0.5)
        no_gz = run(_stand_walk_stand, world_u=lambda t: 0.08, T=20, zoom_speed_factor=sf)
        assert max(tr.cam_zoom) > 0.3, sf
        assert _zoom_model_error(tr) < 0.7 * _zoom_model_error(no_gz), sf
        settled = [i for i in range(38, len(tr.t)) if abs(tr.cam_zoom[i] - tr.cam_zoom[i - 38]) < 1e-3]
        assert settled, sf
        assert max(abs(math.exp(tr.model_zoom[i] - tr.cam_zoom[i]) - 1) for i in settled) < 0.05, sf


def test_gz_correction_is_partial_and_bounded():
    """Один ответ #GZ сдвигает модель на GZ_GAIN расхождения, но не больше GZ_MAX_STEP (ln)."""
    from ptztracker.camera import protocol as P
    from ptztracker.control.camera_model import default_calibration
    from ptztracker.control.framing import GZ_GAIN, GZ_MAX_STEP, FramingController, FramingInput

    ctrl = FramingController(COMPOSITIONS["waist_center"], E, default_calibration(), 0.15)
    span = P.LENS_MAX - P.LENS_MIN
    step = lambda t, pos: ctrl.step(FramingInput(now=t, t_capture=None, measure=None, zoom_pos=pos, zoom_pos_t=t))  # noqa: E731
    step(0.0, P.LENS_MIN)  # опора: k = 1
    small = round(0.2 / math.log(20) * span)  # камера на ~0,2 ln дальше модели
    step(0.04, P.LENS_MIN + small)
    want = small / span * math.log(20)
    assert abs(ctrl.zdyn.position(0.04) - GZ_GAIN * want) < 1e-9
    step(0.08, P.LENS_MAX)  # расхождение ~3 ln — поправка упирается в предел
    assert abs(ctrl.zdyn.position(0.08) - GZ_GAIN * want - GZ_MAX_STEP) < 1e-9


def test_no_zoom_in_while_walking():
    """Наезд прекращается, как только человек пошёл: не дожидаясь плавного спада скорости зума."""
    walk_at = 4.0
    tr = run(lambda t: AX + 0.25 * max(t - walk_at, 0.0), world_u=lambda t: 0.08, T=7, cmd_delay=QUEUE_MAX)
    zooming_in = [t for t, z in zip(tr.t, tr.zoom) if z > 0]
    assert zooming_in and min(zooming_in) < walk_at  # человек стоял — наезд шёл
    # 0,6 с на «пошёл» (WALK_WINDOW_S 0,4 + кадр): переминание при наезде не считается ходьбой, а отличить
    # его можно только по смещению в одну сторону (часть 2, задача 6); наезд после начала ходьбы вырастает не больше чем на 8% (замер 7%, камера встаёт через 0,3 с)
    moving = [i for i, (t, s) in enumerate(zip(tr.t, tr.state)) if t > walk_at + 0.6 and s == "moving"]
    assert moving
    assert all(tr.zoom[i] <= 0 for i in moving)
    assert sum(z * CTRL_DT for t, z in zip(tr.t, tr.zoom) if walk_at < t <= walk_at + 0.6) <= 0.08


# --- сторож раскачки: только настоящие развороты (часть 1, задача 2) ---------------

SOFT = E.soft_x


def _square_swing(w, v, amp, grow=1.15, half=1.0, T=8.0, zoom_feed=None, apc=None, apc_dt=0.5, target=None):
    """Команда ±v со сменой знака каждые half с; ошибка того же знака, её пик растёт в grow раз за полупериод.

    zoom_feed(v) — какая часть команды пришла от доворота за зумом; apc(t) — положение камеры по #APC;
    target(t) — где человек «в мире» (мёртвая зона — E.dead_x). Возвращает момент, когда сторож решил
    «раскачка» (None — молчит).
    """
    t, next_apc = 0.0, 0.0
    while t < T:
        n = int(t / half)
        sgn = 1 if n % 2 == 0 else -1
        phase = (t - n * half) / half
        err = sgn * amp * grow**n * math.sin(math.pi * phase)
        if apc is not None and t >= next_apc - 1e-9:
            next_apc += apc_dt
            w.camera_at(t, apc(t))
        w.update(t, sgn * v, err, SOFT, 0.0 if zoom_feed is None else zoom_feed(sgn * v),
                 None if target is None else target(t), E.dead_x)
        if w.swinging:
            return t
        t = round(t + CTRL_DT, 6)
    return None


def test_watchdog_ignores_reversals_below_moving_speed():
    """Часть 0, B3: 61% «разворотов» — команда 50–55, камера стоит (|v| < 0,02 fw/s)."""
    assert _square_swing(SwingWatch(), v=0.025, amp=1.5 * SOFT) is None
    assert _square_swing(SwingWatch(), v=0.1, amp=1.5 * SOFT) is not None  # та же картина на ходу — раскачка
    # наклон: команда в долях высоты, камера едет по коду как 9/16 этой скорости по ширине
    tilt = SwingWatch(fw_per_unit=9 / 16)
    assert _square_swing(tilt, v=0.045, amp=1.5 * E.soft_y) is None


def test_watchdog_ignores_reversals_during_zoom():
    """Часть 0, B3: 81% «разворотов» — во время зума, их делает доворот за наездом/отъездом."""
    # вся команда — доворот за зумом: камера разворачивается вслед за зумом, а не за ошибкой
    assert _square_swing(SwingWatch(), v=0.1, amp=1.5 * SOFT, zoom_feed=lambda v: v) is None
    # доворот за зумом лишь добавляется к настоящей раскачке — она засчитывается
    assert _square_swing(SwingWatch(), v=0.2, amp=1.5 * SOFT, zoom_feed=lambda v: 0.3 * v) is not None


def test_watchdog_needs_large_and_growing_error():
    """Разворот засчитывается, если ошибка между разворотами больше всей мягкой зоны и растёт."""
    assert _square_swing(SwingWatch(), v=0.1, amp=0.9 * SOFT, grow=1.0) is None  # качание внутри мягкой зоны
    assert _square_swing(SwingWatch(), v=0.1, amp=1.5 * SOFT, grow=0.8) is None  # колебание затухает
    assert _square_swing(SwingWatch(), v=0.1, amp=1.5 * SOFT, grow=1.0) is not None  # не затухает


def test_watchdog_confirms_reversals_by_apc():
    """#APC: разворот засчитывается, только если камера за полупериод правда проехала и развернулась."""
    swing = lambda t: 0x8000 - round(400 * math.cos(math.pi * t))  # noqa: E731 — туда-сюда, как команды
    assert _square_swing(SwingWatch(lag=0.0), v=0.1, amp=1.5 * SOFT, apc=swing) is not None
    assert _square_swing(SwingWatch(lag=0.0), v=0.1, amp=1.5 * SOFT, apc=lambda t: 0x8000) is None  # стоит
    assert _square_swing(SwingWatch(lag=0.0), v=0.1, amp=1.5 * SOFT, apc=lambda t: 0x8000 + round(300 * t)) is None
    # #APC пропал — правило по командам
    assert _square_swing(SwingWatch(lag=0.0), v=0.1, amp=1.5 * SOFT, apc=None) is not None


def test_watchdog_still_catches_real_divergence_x2():
    """Таблица скоростей врёт в 2 и 3 раза (очередь 0,13 с) — настоящая раскачка: сторож останавливает.

    То же с опросом #APC раз в 0,5 с: камера правда разворачивается, подтверждение не мешает.
    """
    for rho in (2.0, 3.0):
        for apc in (None, 0.5):
            tr = run(lambda t: AX + 0.2 * min(t, 3.0), T=25, speed_factor=rho, cmd_delay=0.13, apc_period=apc)
            i = next((i for i, s in enumerate(tr.state) if s == "diverged"), None)
            assert i is not None, (rho, apc)
            assert tr.t[i] <= {2.0: 20.0, 3.0: 10.0}[rho], (rho, apc, tr.t[i])  # замер: 18,2 и 5,8 с
            assert all(p == 0 for p in tr.pan[i + 25 :]), (rho, apc)


def test_watchdog_counts_hunting_while_person_stands():
    """Человек стоит (между разворотами в одну сторону сдвинулся меньше мёртвой зоны), а камера ходит
    туда-сюда больше мягкой зоны за полупериод — раскачка при любой ошибке (Review Focus 2)."""
    still = lambda t: 0.1  # noqa: E731
    assert _square_swing(SwingWatch(), v=0.3, amp=0.3 * SOFT, grow=1.0, target=still) is not None
    # человек ходит туда-сюда сам (сдвиг за период больше мёртвой зоны) — камера идёт за ним
    assert _square_swing(SwingWatch(), v=0.3, amp=0.3 * SOFT, grow=1.0, target=lambda t: 0.04 * t) is None
    # камера за полупериод проезжает меньше мягкой зоны — это доводка, а не рыскание
    assert _square_swing(SwingWatch(), v=0.15, amp=0.3 * SOFT, grow=1.0, target=still) is None
    # неизвестно, где человек — только основное правило
    assert _square_swing(SwingWatch(), v=0.3, amp=0.3 * SOFT, grow=1.0) is None


def _walk_then_stand(t):
    return AX + 0.3 * min(t, 4) - 0.3 * max(0.0, min(t - 12, 4))


def test_watchdog_stops_hunting_on_standing_person():
    """Неверная задержка видео у регулятора, «Резко», очередь 0,13 с.

    Часть 0, B4: 0 вместо 0,15 давало качание наклона ±0,12 кадра без конца, пока человек стоит, — сторож
    останавливал на 31 с. С частью 1 (задача 4: торможение по тормозному пути, «пошёл» по скорости и
    направлению) стоящего человека камера больше не «ищет»: после остановки — несколько затухающих
    разворотов (или сторож останавливает сразу, если #APC нет), а когда человек стоит (с 20 с), — не больше
    одного разворота за 20 с. Ошибка 0 вместо 0,3
    раскачивает по-настоящему — сторож останавливает, все оси в ноль. С верной задержкой сторож молчит."""
    for apc in (None, 0.5):
        mild = run(_walk_then_stand, T=40, effort=1.0, ctrl_latency=0.0, cmd_delay=0.13, apc_period=apc)
        i = next((i for i, s in enumerate(mild.state) if s == "diverged"), None)
        if i is None:
            late = [k for k, t in enumerate(mild.t) if t > 20]  # стоит (с 16 с) после перелёта
            assert sign_changes([mild.pan[k] for k in late], WATCHDOG_MOVING_V) <= 1, apc
            assert sign_changes([mild.tilt[k] for k in late], WATCHDOG_MOVING_V) <= 1, apc  # доводка при наезде
        else:
            assert all(p == 0 and q == 0 for p, q in zip(mild.pan[i + 25 :], mild.tilt[i + 25 :])), apc
        bad = run(_walk_then_stand, T=40, effort=1.0, latency=0.3, ctrl_latency=0.0, cmd_delay=0.13, apc_period=apc)
        i = next((i for i, s in enumerate(bad.state) if s == "diverged"), None)
        assert i is not None, apc
        assert all(p == 0 and q == 0 for p, q in zip(bad.pan[i + 25 :], bad.tilt[i + 25 :])), apc
        good = run(_walk_then_stand, T=40, effort=1.0, cmd_delay=0.13, apc_period=apc)
        assert "diverged" not in good.state, apc


def test_watchdog_quiet_on_settle_and_lectern_sway():
    """Остановка после ходьбы (перелёт и медленный возврат) и покачивание у кафедры — не раскачка."""
    for eff in (0.0, 0.5, 1.0):
        for speed in (0.1, 0.25, 0.35):
            tr = run(lambda t, speed=speed: walk_then_stop(t, speed), T=15, effort=eff, cmd_delay=0.13)
            assert "diverged" not in tr.state, (eff, speed)
    for eff, amp, period in ((0.0, 0.06, 3.0), (0.5, 0.025, 2.5), (0.5, 0.03, 3.0), (0.5, 0.04, 2.0),
                             (1.0, 0.025, 2.5)):
        tr = run(lambda t, a=amp, p=period: AX + a * math.sin(2 * math.pi * t / p), T=20, effort=eff, cmd_delay=0.13)
        assert "diverged" not in tr.state, (eff, amp, period)


def test_watchdog_quiet_with_apc_when_calibration_close():
    """С опросом #APC сторож не срабатывает там, где без него молчал (×0,5 и ×1,7)."""
    for rho in (0.5, 1.7):
        tr = run(lambda t: AX + 0.2 * min(t, 3.0), T=15, speed_factor=rho, cmd_delay=QUEUE_MAX, apc_period=0.5)
        assert "diverged" not in tr.state, rho


PART0 = Path(__file__).resolve().parents[1] / "data" / "watchdog" / "part0_swings.json.gz"
REAL_SWING = "cal1_e10_walk"  # B4: «Резко» с задержкой видео 0 из мастера


def _replay_part0():
    """Повтор выжимки записей части 0 через сторож: {прогон: момент срабатывания} (только сработавшие)."""
    segs = json.loads(gzip.decompress(PART0.read_bytes()))
    assert len(segs) == 10
    tripped = {}
    for s in segs:
        pq = list(s["pos"])
        lag = s["dead_time"] + s["tau"]
        wx, wy = SwingWatch(lag=lag), SwingWatch(fw_per_unit=9 / 16, lag=lag)
        for t, pan, tilt, ex, ey, sx, sy, tx, ty, dx, dy in s["ticks"]:
            while pq and pq[0][0] <= t:
                tp, p, q = pq.pop(0)
                wx.camera_at(tp, p)
                wy.camera_at(tp, q)
            wx.update(t, pan, ex, sx, target=tx, dead=dx)
            wy.update(t, tilt, ey, sy, target=ty, dead=dy)
            if wx.swinging or wy.swinging:
                tripped[s["run"]] = t
                break
    return tripped


def test_watchdog_part0_false_alarms_gone():
    """Повтор 10 первых «РАСКАЧЕК» части 0 (сим, 90 с, docs/field/2026-10-22-part0-analysis.md, B1/B3).

    Выжимку собирает tools/extract_swing_fixture.py (формат — там): такты записей до первой раскачки
    (команды pan/tilt, ошибка, зоны, где человек «в мире»), ответы #APC. Сторож молчит на девяти ложных
    тревогах. Ошибка записана старым регулятором, у которого модель зума врала (B2), — испытание жёстче нынешнего.
    Доворот за зумом в записях не выделен (0): это условие проверяет test_watchdog_ignores_reversals_during_zoom.

    Десятая (B4, наклон, «Резко», задержка видео 0) до остановки успела дать только два разворота быстрее
    0,03 fw/s (третий — 0,036 доли высоты/с = 0,02 fw/s, камера на этом коде стоит), поэтому её здесь не поймать
    ни одним правилом с этим порогом. Такое же качание в долгую ловит «человек стоит, камера ходит» —
    test_watchdog_stops_hunting_on_standing_person.
    """
    tripped = _replay_part0()
    assert not {k: v for k, v in tripped.items() if k != REAL_SWING}


# --- догонять без отставания (часть 1, задача 4; Review Focus 1, 2, 5) ---------------

def _walks(v, walk, stand0=4.0, stand=10.0):
    """Стоит stand0, идёт walk со скоростью v (fw/s), стоит stand, идёт обратно, стоит."""
    def f(t):
        if t < stand0:
            return AX
        if t < stand0 + walk:
            return AX + v * (t - stand0)
        if t < stand0 + walk + stand:
            return AX + v * walk
        return AX + v * walk - v * min(t - stand0 - walk - stand, walk)
    return f, [(stand0, walk, 1), (stand0 + walk + stand, walk, -1)], stand0 + 2 * walk + stand + 10.0


def _follow(f, T, lead_in=0.0, **kw):
    """Как Follower: коды по таблице с поправкой, подстройка скорости по #APC/#GZ (опрос раз в 0,5 с).
    lead_in — сначала столько секунд подстройки (_adapt_lead_in), сценарий f сдвинут на это время."""
    g = f if not lead_in else (lambda t: _adapt_lead_in(t) if t < lead_in else f(t - lead_in))
    kw.setdefault("cmd_delay", 0.065)
    return run(g, T=T + lead_in, apc_period=0.5, gz_period=0.5, adapt=True, **kw)


def _phases(tr, walks, lead_in=0.0):
    """По каждой ходьбе: (средняя |ошибка| без первой 1 с, ближе всего к краю, отъезд зума во сколько раз,
    средняя |ошибка| 1–5 с после остановки, развороты камеры за 10 с стояния, самый быстрый ход назад)."""
    out = []
    for ws, wd, sg in walks:
        ws += lead_in
        walk = [i for i, t in enumerate(tr.t) if ws <= t < ws + wd]
        late = [i for i in walk if tr.t[i] >= ws + 1.0]
        stand = [i for i, t in enumerate(tr.t) if ws + wd <= t < ws + wd + 10.0]
        settle = [i for i in stand if tr.t[i] >= ws + wd + 1.0 and tr.t[i] < ws + wd + 5.0]
        k0 = math.exp(tr.cam_zoom[walk[0]])
        out.append(dict(
            mean=sum(abs(tr.img_x[i] - AX) for i in late) / len(late),
            edge=min(min(tr.img_x[i], 1 - tr.img_x[i]) for i in walk + stand[:25]),
            zoom_out=k0 / min(math.exp(tr.cam_zoom[i]) for i in walk + stand[:25]),
            settle=sum(abs(tr.img_x[i] - AX) for i in settle) / len(settle),
            over=max(0.0, max(-(tr.img_x[i] - AX) * sg for i in stand)),
            reversals=sign_changes([tr.pan[i] for i in stand], WATCHDOG_MOVING_V),
            back=max(0.0, max(-tr.pan[i] * sg for i in stand)),
        ))
    return out


def test_lecture_walk_no_lag():
    """Review Focus 1: прохаживается (до 0,35 fw/s ≈ 0,5 м/с на «Поясе»), «Обычно». Без первой 1 с ходьбы
    (раньше камера ответить не может: видео + очередь + мёртвое время + разгон) средняя ошибка ≤ 0,05 кадра,
    человек не ближе 0,15 к краю, зум не отъезжает больше чем в 1,3 раза. Было (до части 1, 0,31 fw/s):
    0,18 / 0,16 / ×1,5."""
    for v in (0.2, 0.31, 0.35):
        f, walks, T = _walks(v, 8.0)
        tr = _follow(f, T)
        assert "diverged" not in tr.state, v
        for p in _phases(tr, walks):
            assert p["mean"] <= 0.05 and p["edge"] >= 0.15 and p["zoom_out"] <= 1.3, (v, p)


def test_lecture_walk_no_lag_with_calibration_error():
    """То же при таблице скоростей, неверной в 0,5 и 2 раза (после 20 с подстройки по #APC)."""
    for rho in (0.5, 2.0):
        f, walks, T = _walks(0.31, 8.0)
        tr = _follow(f, T, lead_in=20.0, speed_factor=rho)
        assert "diverged" not in tr.state, rho
        for p in _phases(tr, walks, 20.0):
            assert p["mean"] <= 0.05 and p["edge"] >= 0.15 and p["zoom_out"] <= 1.3, (rho, p)


def test_fast_walk_stays_in_frame():
    """Review Focus 1, быстрая ходьба (до 1,2 м/с ≈ 0,77 fw/s на «Поясе», с места): человек в кадре всё время
    (нос не дальше 0,08 за краем, ненадолго — плечо и корпус в кадре: с места быстрее не ответить, камера
    трогается через ~0,4 с и разгоняется с τ), зум отъезжает плавно
    (скорость меняется не быстрее ZOOM_ACCEL·ESCAPE_ZOOM_ACCEL, без «туда-обратно»), слежение не сбрасывается.
    Было: 1,25 м/с — человек уходил из кадра на каждой ходьбе вправо."""
    from ptztracker.control.framing import ESCAPE_ZOOM_ACCEL, ZOOM_ACCEL

    for v, walk in ((0.51, 6.0), (0.77, 4.0)):
        for delay in (0.065, QUEUE_MAX):
            f, walks, T = _walks(v, walk)
            tr = _follow(f, T, cmd_delay=delay)
            assert "diverged" not in tr.state and "idle" not in tr.state[10:], (v, delay)
            assert -0.08 <= min(tr.img_x) and max(tr.img_x) <= 1.08, (v, delay, min(tr.img_x), max(tr.img_x))
            dz = [abs(b - a) for a, b in zip(tr.zoom, tr.zoom[1:])]
            assert max(dz) <= ZOOM_ACCEL * ESCAPE_ZOOM_ACCEL * CTRL_DT + 1e-9, (v, delay)
            for ws, wd, _ in walks:  # за ходьбу зум не дёргается туда-обратно
                zs = [z for z, t in zip(tr.zoom, tr.t) if ws <= t < ws + wd + 2.0]
                assert sign_changes(zs, 0.02) <= 1, (v, delay, ws)


def test_no_swing_with_calibration_error_x05_x2():
    """Review Focus 2: таблица скоростей неверна в 0,5…2 раза, очередь до 0,13 с — сторож молчит, за 10 с
    стояния камера разворачивается не больше одного раза (подстройка по #APC, как в Follower)."""
    for rho in (0.5, 1.0, 1.5, 2.0):
        for delay in (0.065, QUEUE_MAX):
            f, walks, T = _walks(0.31, 8.0)
            tr = _follow(f, T, lead_in=20.0, speed_factor=rho, cmd_delay=delay)
            assert "diverged" not in tr.state, (rho, delay)
            assert all(p["reversals"] <= 1 for p in _phases(tr, walks, 20.0)), (rho, delay)


def test_stop_settles_without_snap_back():
    """Review Focus 5: после остановки (до 0,35 fw/s) средняя |ошибка| 1–5 с после остановки ≤ 0,08 кадра,
    назад камера не дёргается: в мягкой зоне — не быстрее RETURN_V_SOFT, за ней — не быстрее RETURN_V_MAX,
    разворот не больше одного. Пиковый перелёт физически ~0,6–0,8·v (видео + очередь + мёртвое время + τ)."""
    from ptztracker.control.framing import RETURN_V_MAX

    for v in (0.2, 0.31, 0.35):
        f, walks, T = _walks(v, 8.0)
        tr = _follow(f, T)
        for p in _phases(tr, walks):
            assert p["settle"] <= 0.08 and p["back"] <= RETURN_V_MAX + 1e-9 and p["reversals"] <= 1, (v, p)
            assert p["over"] <= 0.9 * v, (v, p)


def test_sway_shuffle_does_not_trip_watchdog_or_overswing():
    """Человек переминается ±0,06 кадра (период 3 с) при «Обычно» и «Резко»: сторож молчит, камера ходит
    не шире самого человека (было: в 2,5–5 раз шире — BACKLOG, задача 2)."""
    for eff in (0.5, 1.0):
        for delay in (0.065, QUEUE_MAX):
            tr = run(lambda t: AX + 0.06 * math.sin(2 * math.pi * t / 3.0), T=30, effort=eff, cmd_delay=delay,
                     apc_period=0.5)
            assert "diverged" not in tr.state, (eff, delay)
            c = default_calibration()
            cam = CameraDynamics(c.dead_time, c.tau)
            for t, p in zip(tr.t, tr.pan):
                cam.command(t + delay, p)
            pos = [cam.position(t) for t in tr.t if t > 5]
            assert max(pos) - min(pos) <= 0.12, (eff, delay, max(pos) - min(pos))


def test_edge_zoom_out_only_when_saturated():
    """Часть 1, задача 6: отъезд у края — только когда поворот уже на пределе и ошибка за мягкой зоной растёт.
    Человек вышел из мёртвой зоны к краю, но стоит: камера догоняет поворотом, зум не трогаем.
    Быстрая ходьба (поворот на пределе, человек всё равно уходит) — отъезд есть, плавный."""
    sx = 0.33  # стоит на 0,33 кадра правее якоря: за EDGE_ZOOM_ERR, но не у самого края — поворот справится сам
    tr = _follow(lambda t: AX + sx, 12.0)
    assert "diverged" not in tr.state
    assert min(tr.cam_zoom) >= -0.03, min(tr.cam_zoom)  # отъезда нет
    f, walks, T = _walks(0.77, 4.0)
    tr = _follow(f, T, cmd_delay=0.13)
    assert min(tr.cam_zoom) < -0.1  # а на пределе — отъезжает


def test_image_motion_follows_sent_commands():
    """Модель сдвига картинки для трекера людей: поворот вправо уводит картинку влево, наклон вверх — вниз, зум растягивает."""
    from ptztracker.control.framing import FramingController

    ctl = FramingController(COMPOSITIONS["waist_center"], E, default_calibration(), 0.0)
    assert ctl.image_motion(0.0, 1.0) == (1.0, 0.0, 0.0)
    ctl.x.dyn.command(0.0, 0.1)  # камера вправо, fw/s
    ctl.y.dyn.command(0.0, 0.2)  # камера вверх, fh/s
    ctl.zoom_sent(0.0, 0.3)  # приближение, ln/с
    r, sx, sy = ctl.image_motion(0.0, 3.0)
    assert r > 1.0 and sx < -0.1 and sy > 0.1
    assert ctl.image_motion(0.5, 0.5) == (1.0, 0.0, 0.0)


def test_zoom_in_reaches_composition_size_while_person_sways():
    """Площадка 2026-10-29, «Пояс»: человек стоит, но переминается (±0,02 мира ≈ ±0,06 кадра на размере композиции, период 3 с) — за минуту зум не
    доходил до размера (ошибка размера −1,1…−1,4, зум толчками раз в ~10 с): «стоит» требовало размах меньше
    мёртвой зоны (0,05), а переминание его превышает. Теперь наезд идёт одним плавным движением до размера
    композиции, без «туда-обратно» (разворотов зума нет) и без раскачки."""
    for amp in (0.0, 0.015, 0.02):
        tr = run(lambda t, a=amp: AX + a * math.sin(2 * math.pi * t / 3.0), world_u=lambda t: 0.05, T=40,
                 zoom_pos=0x555, cmd_delay=QUEUE_MAX, gz_period=0.5, apc_period=0.5, adapt=True, cam_he42=True)
        assert "diverged" not in tr.state, amp
        # размер человека в кадре — в пределах ±15% от размера композиции (0,19)
        assert abs(math.log(tr.u[-1] / COMPOSITIONS["waist_center"].u_frac)) <= 0.15, (amp, tr.u[-1])
        # зум — одним движением: не больше одного разворота (вход → стоп → выход)
        zs = [z for z in tr.zoom if abs(z) > 1e-6]
        assert sign_changes(zs, eps=1e-6) == 0, amp
        # и без «стоп — поехал» в такт переминанию (было 8 стартов зума за 12 с)
        starts = sum(1 for a, b in zip(tr.zoom, tr.zoom[1:]) if a <= 1e-6 < b)
        assert starts <= 2, (amp, starts)


def test_zoom_in_stops_at_once_when_person_walks_off():
    """Наезд идёт, и человек пошёл (упреждение): зум встаёт сразу, отъезд — только у края (не «туда-обратно»)."""
    def walk(t):
        return AX + (0.0 if t < 14 else 0.25 * min(t - 14, 3.0) * 0.5)
    tr = run(walk, world_u=lambda t: 0.05, T=24, zoom_pos=0x555, cmd_delay=QUEUE_MAX, gz_period=0.5, apc_period=0.5,
             adapt=True, cam_he42=True)
    assert "diverged" not in tr.state
    assert max(tr.zoom[int(15.5 / CTRL_DT):]) <= 1e-6


def test_every_effort_stable_at_real_queue_and_table_error():
    """Часть 2, задача 6: разгон τ 0,12 с, очередь команд 0,13 с (p95 живого поворота 121–131 мс), таблица
    скоростей неверна в ρ = 0,5…1,4 раза (×0,8–1,07 у живой таблицы, с запасом UNCALIBRATED_SAFETY 1,3 — 0,6–0,85).
    Любой характер 0…1, три сценария (шаг 0,2 fw/s, переминание ±0,06, прохаживание 0,31 fw/s): сторож молчит,
    при переминании камера ходит не шире самого человека, после шага и ходьбы за последние 10 с — не больше
    одного разворота. Было (τ 0,12, модель без очереди): «Обычно» качалось при ρ ≥ 1,2, «Резко» — при ρ ≥ 0,85."""
    f_walk, _, t_walk = _walks(0.31, 8.0)
    scenarios = {
        "step": (lambda t: AX + 0.2 * min(t, 3.0), 20.0),
        "sway": (lambda t: AX + 0.06 * math.sin(2 * math.pi * t / 3.0), 30.0),
        "lecture": (f_walk, t_walk),
    }
    bad = []
    for eff in (0.0, 0.25, 0.5, 0.75, 1.0):
        for rho in (0.5, 0.7, 0.85, 1.07, 1.4):
            for name, (f, T) in scenarios.items():
                tr = run(f, T=T, effort=eff, speed_factor=rho, cmd_delay=0.13, apc_period=0.5)
                if "diverged" in tr.state:
                    bad.append((eff, rho, name, "diverged"))
                    continue
                if name == "sway":  # по командам регулятора: настоящая камера ходит ещё в ρ раз шире
                    c = default_calibration()
                    cam = CameraDynamics(c.dead_time, c.tau)
                    for t, p in zip(tr.t, tr.pan):
                        cam.command(t + 0.13, p)
                    pos = [cam.position(t) for t in tr.t if t > 5]
                    if max(pos) - min(pos) > 0.12:
                        bad.append((eff, rho, name, round(max(pos) - min(pos), 3)))
                else:
                    late = [p for p, t in zip(tr.pan, tr.t) if t > T - 10]
                    if sign_changes(late, WATCHDOG_MOVING_V) > 1:
                        bad.append((eff, rho, name, sign_changes(late, WATCHDOG_MOVING_V)))
    assert not bad, bad
