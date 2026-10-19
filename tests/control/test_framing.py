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
