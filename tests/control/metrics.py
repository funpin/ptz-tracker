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
    """99-й перцентиль |изменения ускорения команды| (fw/s³)."""
    v = [p for t, p in zip(tr.t, tr.pan) if t >= t0]
    dt = 0.04
    acc = [(b - a) / dt for a, b in zip(v, v[1:])]
    jerk = sorted(abs(b - a) / dt for a, b in zip(acc, acc[1:]))
    return jerk[int(0.99 * (len(jerk) - 1))] if jerk else 0.0
