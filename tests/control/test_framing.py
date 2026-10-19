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
        tr = run(lambda t: walk_then_stop(t, speed), T=13)
        after = [i for i, t in enumerate(tr.t) if t > 3.0]
        assert max(abs(tr.img_x[i] - AX) for i in after) <= E.soft_x
        assert min(tr.pan[i] for i in after) >= -SETTLE_V - 1e-9
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
