"""Мерила движения камеры на виртуальном стенде (trace из loop.run)."""

from __future__ import annotations


def reversals(values, eps: float = 0.03) -> int:
    """Сколько раз скорость сменила знак, пройдя через |v| > eps."""
    signs = [1 if v > 0 else -1 for v in values if abs(v) > eps]
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


def _x(tr, t0: float, t1: float = float("inf")) -> list[tuple[float, float]]:
    return [(t, x) for t, x in zip(tr.t, tr.img_x) if t0 <= t <= t1]


def overshoot_after(tr, t_stop: float, direction: int = 1, anchor: float = 0.5) -> float:
    """На сколько кадра камера проехала мимо человека после остановки (человек шёл в сторону direction)."""
    return max(0.0, max((anchor - x) * direction for _, x in _x(tr, t_stop)))


def settle_time(tr, t_stop: float, tol: float = 0.05, anchor: float = 0.5) -> float:
    """Через сколько секунд после t_stop человек вошёл в ±tol от метки и больше не выходил."""
    pts = _x(tr, t_stop)
    last_out = next((t for t, x in reversed(pts) if abs(x - anchor) > tol), None)
    return 0.0 if last_out is None else last_out - t_stop


def moving_share(tr, t0: float, v_eps: float = 0.01) -> float:
    pts = [v for t, v in zip(tr.t, tr.pan) if t >= t0]
    return sum(1 for v in pts if abs(v) > v_eps) / max(1, len(pts))


def mean_error(tr, t0: float, t1: float = float("inf"), anchor: float = 0.5) -> float:
    pts = _x(tr, t0, t1)
    return sum(abs(x - anchor) for _, x in pts) / max(1, len(pts))


def peak_error(tr, t0: float, t1: float = float("inf"), anchor: float = 0.5) -> float:
    return max(abs(x - anchor) for _, x in _x(tr, t0, t1))


def jerk_p99(tr, t0: float = 0.0) -> float:
    """99-й перцентиль рывка настоящего движения камеры (доли кадра/с³): то, что видно в картинке."""
    v = [p for t, p in zip(tr.t, tr.cam_v) if t >= t0]
    dt = 0.04
    acc = [(b - a) / dt for a, b in zip(v, v[1:])]
    jerk = sorted(abs(b - a) / dt for a, b in zip(acc, acc[1:]))
    return jerk[int(0.99 * (len(jerk) - 1))] if jerk else 0.0


def camera_reversals_per_min(tr, t0: float = 2.0, v_eps: float = 0.06) -> float:
    """Сколько раз в минуту камера на самом деле поехала в другую сторону (медленнее v_eps, доли кадра/с, глазу не заметно)."""
    pts = [(t, v) for t, v in zip(tr.t, tr.cam_v) if t >= t0]
    signs = [1 if v > 0 else -1 for _, v in pts if abs(v) > v_eps]
    return 60.0 * sum(1 for a, b in zip(signs, signs[1:]) if a != b) / max(1e-6, pts[-1][0] - pts[0][0])


def speed_variation_per_min(tr, t0: float = 2.0) -> float:
    """Сумма изменений настоящей скорости камеры за минуту (доли кадра/с): чем больше, тем беспокойнее картинка."""
    pts = [(t, v) for t, v in zip(tr.t, tr.cam_v) if t >= t0]
    return 60.0 * sum(abs(b[1] - a[1]) for a, b in zip(pts, pts[1:])) / max(1e-6, pts[-1][0] - pts[0][0])


# --- «глазами оператора» (запись F9 2026-11-10): то, что видно в картинке, а прежние мерила не ловили ---


def _span(ts) -> float:
    """Длительность ряда с учётом последнего шага."""
    return (ts[-1] - ts[0]) * len(ts) / max(1, len(ts) - 1) if len(ts) > 1 else 0.0


def zoom_pumps_per_min(tr, t0: float = 2.0, ratio: float = 1.25) -> float:
    """Сколько раз в минуту зум «отъехал и вернулся»: масштаб упал в ratio раз от достигнутого и снова вырос в ratio раз."""
    import math

    pts = [(t, math.exp(z)) for t, z in zip(tr.t, tr.cam_zoom) if t >= t0]
    count, out, ext = 0, False, pts[0][1]
    for _, k in pts:
        if not out:
            ext = max(ext, k)
            if k < ext / ratio:
                out, ext = True, k
        else:
            ext = min(ext, k)
            if k > ext * ratio:
                count, out, ext = count + 1, False, k
    return 60.0 * count / max(1e-6, _span([t for t, _ in pts]))


def zoom_moving_share(tr, t0: float = 2.0, eps: float = 0.02) -> float:
    """Доля времени, когда зум камеры едет (быстрее eps ln/с)."""
    pts = [(t, z) for t, z in zip(tr.t, tr.cam_zoom) if t >= t0]
    moving = sum(1 for a, b in zip(pts, pts[1:]) if abs(b[1] - a[1]) > eps * (b[0] - a[0]))
    return moving / max(1, len(pts) - 1)


def off_frame_share(tr, t0: float = 2.0, margin: float = 0.1) -> float:
    """Доля времени, когда человека нет в кадре или он ближе margin к боковому краю."""
    pts = [(x, s) for t, x, s in zip(tr.t, tr.img_x, tr.seen) if t >= t0]
    return sum(1 for x, s in pts if not s or x < margin or x > 1 - margin) / max(1, len(pts))


def overtake_max(tr, world_x, anchor: float = 0.5, window: float = 0.5, v_min: float = 0.03, hold: float = 3.0) -> float:
    """На сколько кадра камера обогнала человека: он по ту сторону метки, откуда шёл (на ходу и hold секунд после).

    world_x — настоящее положение человека; направление — куда он сместился за window (быстрее v_min)."""
    best, direction, t_dir = 0.0, 0, float("-inf")
    for t, x in zip(tr.t, tr.img_x):
        d = world_x(t) - world_x(max(tr.t[0], t - window))
        if abs(d) > v_min * window:
            direction, t_dir = (1 if d > 0 else -1), t
        elif t - t_dir > hold:
            direction = 0
        best = max(best, (anchor - x) * direction)
    return best


def outside_zone_share(tr, t0: float = 2.0) -> float:
    """Доля времени, когда голова по горизонтали дальше границы зоны оператора от точки (tr.zone, tr.anchor)."""
    pts = [x for t, x in zip(tr.t, tr.img_x) if t >= t0]
    return sum(1 for x in pts if abs(x - tr.anchor[0]) > tr.zone[0]) / max(1, len(pts))


def zone_exit_depth(tr, t0: float = 2.0) -> float:
    """Наибольший выход головы за границу зоны по горизонтали, доли кадра (0 — не выходила)."""
    return max(0.0, max(abs(x - tr.anchor[0]) - tr.zone[0] for t, x in zip(tr.t, tr.img_x) if t >= t0))


def command_lag_share(tr, t0: float = 2.0, axis: int = 0, codes: int = 2) -> float:
    """Доля времени движения, когда камера исполняет не тот код, что решил регулятор (от codes кодов мимо)."""
    pts = [(c[axis], a[axis]) for t, c, a in zip(tr.t, tr.codes, tr.applied) if t >= t0 and c[axis] != 50]
    return sum(1 for want, got in pts if abs(want - got) >= codes) / max(1, len(pts))


def stop_delay_max(tr, axis: int = 0) -> float:
    """Самая долгая задержка «регулятор решил стоп → камера получила стоп», секунды."""
    best, since, prev = 0.0, None, 50
    for t, c, a in zip(tr.t, tr.codes, tr.applied):
        if c[axis] == 50 and prev != 50 and a[axis] != 50:
            since = t
        elif c[axis] != 50:
            since = None
        if since is not None and a[axis] == 50:
            best, since = max(best, t - since), None
        prev = c[axis]
    return best
