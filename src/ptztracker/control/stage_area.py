"""Область сцены: рамка в зале (градусы от середины #APC), в которой программа сама ищет человека.

Точка кадра ↔ направление в зале — пинхол без бочки объектива: pan > 0 — вправо, tilt > 0 — вверх (как у
sim.projection); x, y — доли кадра от левого верхнего угла. Спека: docs/superpowers/specs/2026-11-13-stage-area-design.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ptztracker.camera import he42
from ptztracker.camera import protocol as P

ASPECT = 9 / 16  # высота кадра к ширине
PAN_MAX_DEG, TILT_MAX_DEG = 180.0, 90.0
NOSE, NOSE_MIN_CONF = 0, 0.3
HEAD_BELOW_TOP = 0.1  # нос не найден: голова — середина верха рамки, ниже на эту долю её высоты
OUTLINE_MARGIN_DEG = 10.0  # контур рисуем не дальше этого за краем кадра: всё, что дальше, в кадр не попадает
MIN_SIZE_DEG = 1.0  # область не уже и не ниже
FIRST_BOTTOM = 0.7  # первая область: нижний край на этой доле высоты кадра, бока и верх — за кадром («линия»)
FIT_INSET = 0.08  # вернуть сторону в кадр: на этой доле кадра от его края
OUTLINE_STEPS = 12  # точек на сторону контура (край по высоте на широком угле — дуга, а не прямая)


@dataclass(frozen=True)
class CameraView:
    """Куда и как широко смотрит камера."""

    pan_deg: float
    tilt_deg: float
    hfov_deg: float

    @property
    def vfov_deg(self) -> float:
        return 2 * math.degrees(math.atan(ASPECT * math.tan(math.radians(self.hfov_deg) / 2)))


def view_from(apc: tuple[int, int], zoom_pos: int) -> CameraView:
    """Вид по ответам камеры: положение #APC и зум #GZ."""
    frac = min(1.0, max(0.0, (zoom_pos - P.LENS_MIN) / (P.LENS_MAX - P.LENS_MIN)))
    return CameraView((apc[0] - he42.APC_CENTER) * he42.APC_DEG_PER_UNIT,
                      (apc[1] - he42.APC_CENTER) * he42.APC_DEG_PER_UNIT, he42.hfov_deg(frac))


def moved_view(view: CameraView, motion: tuple[float, float, float]) -> CameraView:
    """Вид после того, как камера сама сдвинула картинку: motion = (во сколько раз увеличила, сдвиг x, сдвиг y) в
    долях кадра, как у FramingController.image_motion — точка p теперь стоит в 0,5 + r·(p − 0,5) + сдвиг."""
    r, sx, sy = motion
    if r == 1.0 and not sx and not sy:
        return view
    pan, tilt = to_world(view, 0.5 - sx / r, 0.5 - sy / r)  # что теперь в центре кадра
    hfov = 2 * math.degrees(math.atan(math.tan(math.radians(view.hfov_deg) / 2) / r))
    return CameraView(pan, tilt, hfov)


def hfov_of_zoom_x(zoom_x: float) -> float:
    """Угол обзора по масштабу зума (1 — широкий угол, he42.ZOOM_RATIO — теле)."""
    return he42.hfov_deg(min(1.0, max(0.0, math.log(max(zoom_x, 1e-9)) / math.log(he42.ZOOM_RATIO))))


def _trig(view: CameraView) -> tuple[float, float, float, float, float]:
    p, t = math.radians(view.pan_deg), math.radians(view.tilt_deg)
    return math.cos(p), math.sin(p), math.cos(t), math.sin(t), 2 * math.tan(math.radians(view.hfov_deg) / 2)


def to_world(view: CameraView, x: float, y: float) -> tuple[float, float]:
    """Точка кадра → направление в зале (pan°, tilt°)."""
    cp, sp, ct, st, k = _trig(view)
    xu, yu = (x - 0.5) * k, (y - 0.5) * k * ASPECT
    y1, z1 = ct * yu - st, st * yu + ct  # наклон камеры
    wx, wz = cp * xu + sp * z1, -sp * xu + cp * z1  # поворот
    return math.degrees(math.atan2(wx, wz)), math.degrees(math.atan2(-y1, math.hypot(wx, wz)))


def to_frame(view: CameraView, pan_deg: float, tilt_deg: float) -> tuple[float, float] | None:
    """Направление в зале → точка кадра (может лежать вне 0…1); None — направление позади камеры."""
    cp, sp, ct, st, k = _trig(view)
    a, b = math.radians(pan_deg), math.radians(tilt_deg)
    wx, wy, wz = math.sin(a) * math.cos(b), -math.sin(b), math.cos(a) * math.cos(b)
    x1, z1 = cp * wx - sp * wz, sp * wx + cp * wz
    yc, zc = ct * wy + st * z1, -st * wy + ct * z1
    if zc <= 1e-6:
        return None
    return 0.5 + x1 / zc / k, 0.5 + yc / zc / (k * ASPECT)


@dataclass(frozen=True)
class StageArea:
    pan0: float
    pan1: float
    tilt0: float  # нижний край
    tilt1: float  # верхний край
    on: bool = True

    def contains(self, pan_deg: float, tilt_deg: float) -> bool:
        return self.pan0 <= pan_deg <= self.pan1 and self.tilt0 <= tilt_deg <= self.tilt1

    def holds(self, view: CameraView, x: float, y: float) -> bool:
        """Точка кадра лежит в области."""
        return self.contains(*to_world(view, x, y))

    def to_json(self) -> dict:
        return {"pan": [self.pan0, self.pan1], "tilt": [self.tilt0, self.tilt1], "on": self.on}

    @classmethod
    def from_json(cls, d) -> StageArea | None:
        """Область из настроек; всё, что на неё не похоже, — «не задана»."""
        try:
            (p0, p1), (t0, t1) = d["pan"], d["tilt"]
            p0, p1, t0, t1 = float(p0), float(p1), float(t0), float(t1)
        except (TypeError, ValueError, KeyError):
            return None
        if not all(math.isfinite(v) for v in (p0, p1, t0, t1)):
            return None
        if not (-PAN_MAX_DEG <= p0 < p1 <= PAN_MAX_DEG and -TILT_MAX_DEG <= t0 < t1 <= TILT_MAX_DEG):
            return None
        return cls(p0, p1, t0, t1, on=d.get("on", True) is not False)

    def outline(self, view: CameraView) -> list[tuple[float, float]]:
        """Контур области в кадре (доли кадра, по кругу). Стороны, ушедшие далеко за кадр, подрезаны: рисовать их
        незачем, а направление позади камеры в кадр не переводится."""
        hp, ht = view.hfov_deg / 2 + OUTLINE_MARGIN_DEG, view.vfov_deg / 2 + OUTLINE_MARGIN_DEG
        p0, p1 = max(self.pan0, view.pan_deg - hp), min(self.pan1, view.pan_deg + hp)
        t0, t1 = max(self.tilt0, view.tilt_deg - ht), min(self.tilt1, view.tilt_deg + ht)
        if p0 >= p1 or t0 >= t1:
            return []
        n = OUTLINE_STEPS
        ring = ([(p0 + (p1 - p0) * i / n, t1) for i in range(n)] + [(p1, t1 + (t0 - t1) * i / n) for i in range(n)]
                + [(p1 + (p0 - p1) * i / n, t0) for i in range(n)] + [(p0, t0 + (t1 - t0) * i / n) for i in range(n)])
        pts = [to_frame(view, p, t) for p, t in ring]
        return [pt for pt in pts if pt is not None]


def default_area(view: CameraView) -> StageArea:
    """Область, с которой начинается настройка: всё, что выше 70 % высоты кадра."""
    return StageArea(-PAN_MAX_DEG, PAN_MAX_DEG, to_world(view, 0.5, FIRST_BOTTOM)[1], TILT_MAX_DEG)


def _mid(area: StageArea, view: CameraView) -> tuple[float, float]:
    """Направление внутри области, ближайшее к центру кадра: от него меряются середины сторон."""
    return (min(area.pan1, max(area.pan0, view.pan_deg)), min(area.tilt1, max(area.tilt0, view.tilt_deg)))


def handles(area: StageArea, view: CameraView) -> dict[str, tuple[float, float]]:
    """Ручки сторон, попавших в кадр: {сторона: точка кадра}. Стороны — "pan0", "pan1", "tilt0", "tilt1"."""
    mp, mt = _mid(area, view)
    out = {}
    for side, (p, t) in (("pan0", (area.pan0, mt)), ("pan1", (area.pan1, mt)),
                         ("tilt0", (mp, area.tilt0)), ("tilt1", (mp, area.tilt1))):
        if abs(p - view.pan_deg) < 89 and abs(t - view.tilt_deg) < 89:
            pt = to_frame(view, p, t)
            if pt is not None and 0.0 <= pt[0] <= 1.0 and 0.0 <= pt[1] <= 1.0:
                out[side] = pt
    return out


def side_at(area: StageArea, view: CameraView, x: float, y: float, tol: float) -> str | None:
    """Сторона области под точкой кадра (не дальше tol долей ширины кадра) — за неё тянут."""
    pan, tilt = to_world(view, x, y)
    best, best_d = None, tol
    for side, (p, t) in (("pan0", (area.pan0, tilt)), ("pan1", (area.pan1, tilt)),
                         ("tilt0", (pan, area.tilt0)), ("tilt1", (pan, area.tilt1))):
        if abs(p - view.pan_deg) >= 89 or abs(t - view.tilt_deg) >= 89:
            continue
        pt = to_frame(view, p, t)
        if pt is None:
            continue
        d = math.hypot(pt[0] - x, (pt[1] - y) * ASPECT)
        if d < best_d:
            best, best_d = side, d
    return best


def drag_side(area: StageArea, side: str, pan_deg: float, tilt_deg: float) -> StageArea:
    """Область после того, как сторону side перетащили в направление (pan, tilt)."""
    p0, p1, t0, t1 = area.pan0, area.pan1, area.tilt0, area.tilt1
    if side == "pan0":
        p0 = max(-PAN_MAX_DEG, min(pan_deg, p1 - MIN_SIZE_DEG))
    elif side == "pan1":
        p1 = min(PAN_MAX_DEG, max(pan_deg, p0 + MIN_SIZE_DEG))
    elif side == "tilt0":
        t0 = max(-TILT_MAX_DEG, min(tilt_deg, t1 - MIN_SIZE_DEG))
    elif side == "tilt1":
        t1 = min(TILT_MAX_DEG, max(tilt_deg, t0 + MIN_SIZE_DEG))
    return StageArea(p0, p1, t0, t1, area.on)


def fit_hidden_sides(area: StageArea, view: CameraView) -> StageArea:
    """Стороны, ушедшие за кадр, — обратно в кадр, чуть от края (двойной клик при настройке)."""
    left, top = to_world(view, FIT_INSET, 0.5)[0], to_world(view, 0.5, FIT_INSET)[1]
    right, bottom = to_world(view, 1 - FIT_INSET, 0.5)[0], to_world(view, 0.5, 1 - FIT_INSET)[1]
    seen = handles(area, view)
    out = area
    for side, (pan, tilt) in (("pan0", (left, 0.0)), ("pan1", (right, 0.0)), ("tilt0", (0.0, bottom)), ("tilt1", (0.0, top))):
        if side not in seen:
            out = drag_side(out, side, pan, tilt)
    return out


def flags(area: StageArea | None, view: CameraView | None, people, frame_size) -> list[bool] | None:
    """Для каждого человека: голова в области сцены. None — область не действует (не задана, выключена или вид
    камеры неизвестен): тогда все считаются «на сцене»."""
    if area is None or not area.on or view is None:
        return None
    w, h = float(frame_size[0]), float(frame_size[1])
    out = []
    for p in people:
        x, y = head_point(p)
        out.append(area.holds(view, x / w, y / h))
    return out


def head_point(person) -> tuple[float, float]:
    """Голова человека в пикселях кадра: нос, если найден уверенно; иначе середина верха рамки, чуть ниже."""
    kp = person.keypoints
    if kp is not None and len(kp) > NOSE and float(kp[NOSE][2]) >= NOSE_MIN_CONF:
        return float(kp[NOSE][0]), float(kp[NOSE][1])
    x1, y1, x2, y2 = (float(v) for v in person.box)
    return (x1 + x2) / 2, y1 + HEAD_BELOW_TOP * (y2 - y1)
