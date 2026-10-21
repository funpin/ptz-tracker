"""Графики повтора (PNG 1600×900) средствами cv2: ошибка кадра, коды pan/tilt «было/стало», зум.

Подписи латиницей: шрифты Hershey в cv2 не знают кириллицы.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ptztracker.recorder.replay import MAX_DT_S, ReplayResult

W, H = 1600, 900
LEFT, RIGHT, TOP, GAP, BOTTOM = 80, 20, 30, 40, 50
FONT = cv2.FONT_HERSHEY_SIMPLEX
BG, AXIS, TEXT = (255, 255, 255), (150, 150, 150), (40, 40, 40)
OFF_BAND, LOST_BAND = (225, 225, 225), (200, 200, 255)  # BGR: серый — слежение выкл, розовый — цель потеряна
BLUE, BLUE_LIGHT = (180, 90, 20), (235, 200, 160)
ORANGE, ORANGE_LIGHT = (20, 120, 240), (160, 205, 250)
GREEN, PURPLE = (60, 160, 40), (160, 60, 150)


class _Panel:
    def __init__(self, img, y0: int, y1: int, t0: float, t1: float, lo: float, hi: float, title: str):
        self.img, self.y0, self.y1 = img, y0, y1
        self.x0, self.x1 = LEFT, W - RIGHT
        self.t0, self.t1 = t0, max(t1, t0 + 1e-6)
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        self.lo, self.hi = lo, hi
        self.title = title

    def px(self, t: float) -> int:
        return int(round(self.x0 + (t - self.t0) / (self.t1 - self.t0) * (self.x1 - self.x0)))

    def py(self, v: float) -> int:
        v = min(self.hi, max(self.lo, v))
        return int(round(self.y1 - (v - self.lo) / (self.hi - self.lo) * (self.y1 - self.y0)))

    def band(self, a: float, b: float, color) -> None:
        xa, xb = max(self.x0, self.px(a)), min(self.x1, self.px(b))
        if xb > xa:
            cv2.rectangle(self.img, (xa, self.y0), (xb, self.y1), color, -1)

    def frame(self, ticks: list[float]) -> None:
        cv2.rectangle(self.img, (self.x0, self.y0), (self.x1, self.y1), AXIS, 1)
        cv2.putText(self.img, self.title, (self.x0, self.y0 - 8), FONT, 0.55, TEXT, 1, cv2.LINE_AA)
        for v in ticks:
            y = self.py(v)
            cv2.line(self.img, (self.x0 - 4, y), (self.x0, y), AXIS, 1)
            cv2.putText(self.img, f"{v:g}", (6, y + 5), FONT, 0.45, TEXT, 1, cv2.LINE_AA)

    def hline(self, v: float) -> None:
        cv2.line(self.img, (self.x0, self.py(v)), (self.x1, self.py(v)), AXIS, 1)

    def line(self, ts, vs, color, thick: int = 1, breaks=None) -> None:
        """Ломаная; разрывы — по паузе больше MAX_DT_S, по None и по смене номера в breaks."""
        prev = None
        for i, (t, v) in enumerate(zip(ts, vs)):
            if v is None:
                prev = None
                continue
            p = (self.px(t), self.py(v))
            if prev is not None and t - prev[0] <= MAX_DT_S and (breaks is None or breaks[i] == breaks[i - 1]):
                cv2.line(self.img, prev[1], p, color, thick, cv2.LINE_AA)
            else:
                cv2.circle(self.img, p, 1, color, -1)
            prev = (t, p)

    def steps(self, ts, vs, color, thick: int = 1) -> None:
        """Ступеньки: значение держится до следующего (положение камеры между опросами)."""
        for i in range(len(ts)):
            t1 = ts[i + 1] if i + 1 < len(ts) else self.t1
            p0, p1 = (self.px(ts[i]), self.py(vs[i])), (self.px(t1), self.py(vs[i]))
            cv2.line(self.img, p0, p1, color, thick, cv2.LINE_AA)
            if i + 1 < len(ts):
                cv2.line(self.img, p1, (p1[0], self.py(vs[i + 1])), color, thick, cv2.LINE_AA)

    def legend(self, items: list[tuple[str, tuple[int, int, int]]]) -> None:
        x = self.x1
        y = self.y0 - 8  # над панелью: не закрывает графики
        for label, color in reversed(items):
            (tw, _), _ = cv2.getTextSize(label, FONT, 0.45, 1)
            x -= tw
            cv2.putText(self.img, label, (x, y), FONT, 0.45, TEXT, 1, cv2.LINE_AA)
            x -= 26
            cv2.line(self.img, (x, y - 5), (x + 20, y - 5), color, 3)
            x -= 16


def _scaled(vals: list[float], lo: float, hi: float) -> list[float]:
    """Абсолютное положение камеры → в диапазон панели (по своим min/max): видно форму движения."""
    a, b = min(vals), max(vals)
    if b - a < 1e-9:
        return [(lo + hi) / 2] * len(vals)
    return [lo + (v - a) / (b - a) * (hi - lo) for v in vals]


def _off_intervals(res: ReplayResult, t0: float, t1: float) -> list[tuple[float, float]]:
    out, cur = [], t0
    for a, b in sorted(res.segments):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if t1 > cur:
        out.append((cur, t1))
    return out


def render(res: ReplayResult, path: Path | str) -> Path:
    path = Path(path)
    img = np.full((H, W, 3), BG, np.uint8)
    t0, t1 = res.t_start, max(res.t_end, res.t_start + 1.0)
    rel = lambda ts: [t - t0 for t in ts]  # noqa: E731
    span = t1 - t0
    ph = (H - TOP - BOTTOM - 2 * GAP) // 3
    ys = [TOP + i * (ph + GAP) for i in range(3)]

    emax = max([0.1] + [abs(v) for v in res.err_x + res.err_y])
    codes = [c for cs in (res.codes_rec, res.codes_new) for c in cs]
    pt = [v for c in codes for v in c[:2]] + [40, 60]  # не уже ±10 кодов вокруг «стоп»
    zc = [c[2] for c in codes] + [40, 60]
    panels = [
        _Panel(img, ys[0], ys[0] + ph, 0.0, span, -emax, emax, "error (frame fractions)"),
        _Panel(img, ys[1], ys[1] + ph, 0.0, span, min(pt) - 2, max(pt) + 2, "pan/tilt codes: recorded vs replay"),
        _Panel(img, ys[2], ys[2] + ph, 0.0, span, min(zc) - 2, max(zc) + 2, "zoom codes: recorded vs replay"),
    ]
    for p in panels:
        for a, b in _off_intervals(res, t0, t1):
            p.band(a - t0, b - t0, OFF_BAND)
        for a, b in res.lost:
            p.band(a - t0, b - t0, LOST_BAND)

    e, c, z = panels
    e.frame([round(-emax, 2), 0, round(emax, 2)])
    e.hline(0)
    ts = rel(res.t)
    e.line(ts, res.err_x, BLUE, 2, res.segment)
    e.line(ts, res.err_y, ORANGE, 2, res.segment)
    e.legend([("err x", BLUE), ("err y", ORANGE), ("follow off", OFF_BAND), ("target lost", LOST_BAND)])

    c.frame([round(c.lo + 2), 50, round(c.hi - 2)])
    c.hline(50)
    c.line(ts, [k[0] for k in res.codes_rec], BLUE_LIGHT, 3, res.segment)
    c.line(ts, [k[1] for k in res.codes_rec], ORANGE_LIGHT, 3, res.segment)
    c.line(ts, [k[0] for k in res.codes_new], BLUE, 1, res.segment)
    c.line(ts, [k[1] for k in res.codes_new], ORANGE, 1, res.segment)
    legend = [("pan rec", BLUE_LIGHT), ("pan new", BLUE), ("tilt rec", ORANGE_LIGHT), ("tilt new", ORANGE)]
    if res.position:
        pts = rel([p[0] for p in res.position])
        c.steps(pts, _scaled([p[1] for p in res.position], c.lo, c.hi), GREEN)
        c.steps(pts, _scaled([p[2] for p in res.position], c.lo, c.hi), PURPLE)
        legend += [("pan pos (scaled)", GREEN), ("tilt pos (scaled)", PURPLE)]
    c.legend(legend)

    z.frame([round(z.lo + 2), 50, round(z.hi - 2)])
    z.hline(50)
    z.line(ts, [k[2] for k in res.codes_rec], BLUE_LIGHT, 3, res.segment)
    z.line(ts, [k[2] for k in res.codes_new], BLUE, 1, res.segment)
    legend = [("zoom rec", BLUE_LIGHT), ("zoom new", BLUE)]
    zpos = [(p[0], p[3]) for p in res.position if p[3] is not None]
    if zpos:
        z.steps(rel([p[0] for p in zpos]), _scaled([p[1] for p in zpos], z.lo, z.hi), GREEN)
        legend.append(("zoom pos (scaled)", GREEN))
    z.legend(legend)

    # ось времени
    step = next((s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120) if span / s <= 16), 300)
    y = ys[2] + ph
    k = 0
    while k * step <= span + 1e-9:
        x = z.px(k * step)
        cv2.line(img, (x, y), (x, y + 5), AXIS, 1)
        cv2.putText(img, f"{k * step:g}", (x - 8, y + 22), FONT, 0.45, TEXT, 1, cv2.LINE_AA)
        k += 1
    cv2.putText(img, "t, s", (W - RIGHT - 30, H - 8), FONT, 0.5, TEXT, 1, cv2.LINE_AA)

    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("cv2.imencode не смог закодировать PNG")
    path.write_bytes(buf.tobytes())  # imwrite не пишет по путям с кириллицей на Windows
    return path
