from loop import CTRL_DT, run, sign_changes

from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile

AX = COMPOSITIONS["waist_center"].anchor[0]
E = EffortProfile.at(0.5)


def test_holds_still_when_inside_dead_zone():
    tr = run(lambda t: AX + 0.03, T=5)
    assert all(p == 0 for p in tr.pan)


def test_starts_smoothly_and_accel_limited():
    tr = run(lambda t: AX + 0.2, T=4)
    steps = [abs(b - a) for a, b in zip(tr.pan, tr.pan[1:])]
    assert max(steps) <= E.a_max * CTRL_DT + 1e-9
    assert next(p for p in tr.pan if p != 0) > 0  # человек справа → камера вправо


def test_feedforward_matches_walking_speed():
    tr = run(lambda t: AX + 0.2 * t, T=6)
    late = [i for i, t in enumerate(tr.t) if t > 4]
    assert all(abs(tr.pan[i] - 0.2) < 0.05 for i in late)
    assert all(abs(tr.img_x[i] - AX) < E.soft_x for i in late)


def walk_then_stop(t, speed=0.25, t_stop=3.0):
    return AX + speed * min(t, t_stop)


def test_stops_without_overshoot_after_walk():
    # Тормозной путь камеры (τ≈0.37 с + задержка кадра) физически даёт перелёт ~v·0.6.
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
    def over(ctrl_latency):
        tr = run(lambda t: walk_then_stop(t, 0.35), T=9, latency=0.35, ctrl_latency=ctrl_latency)
        return max(AX - x for x, t in zip(tr.img_x, tr.t) if t > 3.0)

    assert over(0.35) < over(0.0)


def test_no_target_ramps_to_zero():
    tr = run(lambda t: AX + 0.3 * t, visible=lambda t: t < 3, T=7)
    assert tr.pan[-1] == 0 and tr.state[-1] == "idle"
    steps = [abs(b - a) for a, b in zip(tr.pan, tr.pan[1:])]
    assert max(steps) <= E.a_max * CTRL_DT + 1e-9


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


def test_zoom_does_not_hunt():
    import math

    tr = run(lambda t: AX, world_u=lambda t: 0.19 * (1 + 0.08 * math.sin(t * 3)), T=10)
    assert all(z == 0 for z in tr.zoom)


def test_tilt_follows_vertical():
    tr = run(lambda t: AX, world_y=lambda t: 0.25 + 0.15 * min(t, 2) / 2, T=7)
    assert abs(tr.img_y[-1] - 0.25) < E.dead_y and sign_changes(tr.tilt) <= 1


# --- устойчивость (финальное ревью этапа 3) -------------------------------------

import math  # noqa: E402


def test_robust_when_camera_faster_than_model():
    # Калибровка неточна (камера в 1.7 раза быстрее или вдвое медленнее) + очередь 0.13 с — без раскачки
    for rho in (0.5, 1.7):
        tr = run(lambda t: AX + 0.2 * min(t, 3.0), T=15, speed_factor=rho, cmd_delay=0.13)
        assert 0.3 < min(tr.img_x) and max(tr.img_x) < 0.8, rho
        assert sign_changes(tr.pan) <= 2 and "diverged" not in tr.state, rho
    tr = run(lambda t: AX + 0.2 * min(t, 3.0), T=15, speed_factor=1.7, cmd_delay=0.13)
    assert 0.3 < min(tr.img_x) and max(tr.img_x) < 0.75
    assert sign_changes(tr.pan) <= 3 and "diverged" not in tr.state


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
    tr = run(lambda t: AX + 0.25 * max(t - walk_at, 0.0), world_u=lambda t: 0.08, T=7, cmd_delay=0.065)
    zooming_in = [t for t, z in zip(tr.t, tr.zoom) if z > 0]
    assert zooming_in and min(zooming_in) < walk_at  # человек стоял — наезд шёл
    moving = [i for i, (t, s) in enumerate(zip(tr.t, tr.state)) if t > walk_at and s == "moving"]
    assert moving
    assert all(tr.zoom[i] <= 0 for i in moving)


# --- сторож раскачки: только настоящие развороты (часть 1, задача 2) ---------------

import gzip  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

from ptztracker.control.framing import WATCHDOG_REVERSALS, SwingWatch  # noqa: E402

SOFT = E.soft_x


def _square_swing(w, v, amp, grow=1.15, half=1.0, T=8.0, zoom_feed=None, apc=None, apc_dt=0.5):
    """Команда ±v со сменой знака каждые half с; ошибка того же знака, её пик растёт в grow раз за полупериод.

    zoom_feed(v) — какая часть команды пришла от доворота за зумом; apc(t) — положение камеры по #APC.
    Возвращает момент, когда засчитано WATCHDOG_REVERSALS разворотов (None — сторож молчит).
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
        w.update(t, sgn * v, err, SOFT, 0.0 if zoom_feed is None else zoom_feed(sgn * v))
        if len(w.flips) >= WATCHDOG_REVERSALS:
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
            assert all(p == 0 for p in tr.pan[i + 25 :]), (rho, apc)


def test_watchdog_quiet_with_apc_when_calibration_close():
    """С опросом #APC сторож не срабатывает там, где без него молчал (×0,5 и ×1,7)."""
    for rho in (0.5, 1.7):
        tr = run(lambda t: AX + 0.2 * min(t, 3.0), T=15, speed_factor=rho, cmd_delay=0.13, apc_period=0.5)
        assert "diverged" not in tr.state, rho


PART0 = Path(__file__).resolve().parents[1] / "data" / "watchdog" / "part0_swings.json.gz"
REAL_SWING = "cal1_e10_walk"  # B4: «Резко» с задержкой видео 0 из мастера


def test_watchdog_part0_false_alarms_gone():
    """Повтор 10 первых «РАСКАЧЕК» части 0 (сим, 90 с, docs/field/2026-10-22-part0-analysis.md, B1/B3).

    В файле — такты записей до первой раскачки: команды pan/tilt и ошибка регулятора, ушедшие в камеру
    команды зума и ответы #APC. Сторож с новыми правилами молчит на всех девяти ложных тревогах.
    Ошибка записана старым регулятором, у которого модель зума врала (B2), — испытание жёстче нынешнего.

    Десятая (B4) — затухающее качание наклона: пики ошибки 1,17 → 0,90 → 0,71 мягкой зоны, ниже неё
    после первого разворота. По новым правилам это не раскачка; причину (задержка 0 из мастера) лечит задача 3.
    """
    from ptztracker.control.camera_model import CameraDynamics

    segs = json.loads(gzip.decompress(PART0.read_bytes()))
    assert len(segs) == 10
    tripped = {}
    for s in segs:
        zd = CameraDynamics(s["dead_time"], s["tau"])
        zq, pq = list(s["zoom"]), list(s["pos"])
        lag = s["dead_time"] + s["tau"]
        wx, wy = SwingWatch(lag=lag), SwingWatch(fw_per_unit=9 / 16, lag=lag)
        for t, pan, tilt, ex, ey, sx, sy in s["ticks"]:
            while zq and zq[0][0] <= t:
                zd.command(*zq.pop(0))
            while pq and pq[0][0] <= t:
                tp, p, q = pq.pop(0)
                wx.camera_at(tp, p)
                wy.camera_at(tp, q)
            wx.update(t, pan, ex, sx)
            wy.update(t, tilt, ey, sy)
            if max(len(wx.flips), len(wy.flips)) >= WATCHDOG_REVERSALS:
                tripped[s["run"]] = t
                break
    false_alarms = {k: v for k, v in tripped.items() if k != REAL_SWING}
    assert not false_alarms
