"""Сопровождение людей между кадрами: постоянные номера, выбранная цель, узнавание цели по внешности.

Подход как у ByteTrack: слабые детекции только продолжают уже известных людей,
новый человек появляется лишь при уверенной детекции. Для цели дополнительно
сверяется «внешность» (цветовая гистограмма торса), чтобы рамка не перескочила
на соседа, и чтобы цель узнавалась, если она пропала и появилась снова.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from ptztracker.vision.types import KP_L_HIP, KP_L_SHOULDER, KP_R_HIP, KP_R_SHOULDER, Person

KP_MIN_CONF = 0.3
ISOLATION_IOU = 0.1
VELOCITY_ALPHA = 0.5
MIN_VELOCITY_DT = 1 / 30  # кадры из RTSP приходят пачками — не делить сдвиг на миллисекунды
MAX_SPEED_PX_S = 1500.0
MAX_EXTRAPOLATION_S = 0.3  # дальше прогноз не тянем: человек за кафедрой обычно стоит на месте


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _torso_mask(p: Person, w: int, h: int) -> tuple[int, int, np.ndarray] | None:
    """Маска торса только в пределах его прямоугольника: (x0, y0, маска). None — торс слишком мал."""
    kp = p.keypoints
    ls, rs = kp[KP_L_SHOULDER], kp[KP_R_SHOULDER]
    lh, rh = kp[KP_L_HIP], kp[KP_R_HIP]
    poly = None
    if ls[2] >= KP_MIN_CONF and rs[2] >= KP_MIN_CONF:
        if lh[2] >= KP_MIN_CONF and rh[2] >= KP_MIN_CONF:
            poly = np.array([ls[:2], rs[:2], rh[:2], lh[:2]], np.float32)
            x0, y0 = poly[:, 0].min(), poly[:, 1].min()
            x1, y1 = poly[:, 0].max(), poly[:, 1].max()
        else:  # крупный план: бёдер не видно — от плеч вниз на 1,2 ширины плеч
            sw = abs(ls[0] - rs[0])
            x0, x1 = min(ls[0], rs[0]), max(ls[0], rs[0])
            y0 = (ls[1] + rs[1]) / 2
            y1 = min(y0 + 1.2 * sw, p.box[3])
    else:
        bx1, by1, bx2, by2 = p.box
        bw, bh = bx2 - bx1, by2 - by1
        x0, x1 = bx1 + bw / 3, bx1 + 2 * bw / 3
        y0, y1 = by1 + 0.2 * bh, by1 + 0.55 * bh
    ix0, iy0 = int(max(0, x0)), int(max(0, y0))
    ix1, iy1 = int(min(w, x1)), int(min(h, y1))
    if ix1 - ix0 < 8 or iy1 - iy0 < 8:
        return None
    mask = np.full((iy1 - iy0, ix1 - ix0), 255, np.uint8)
    if poly is not None:
        mask[:] = 0
        shifted = (poly - np.array([ix0, iy0], np.float32)).astype(np.int32)
        cv2.fillConvexPoly(mask, cv2.convexHull(shifted), 255)
        if cv2.countNonZero(mask) < 64:
            return None
    return ix0, iy0, mask


def torso_histogram(image: np.ndarray, p: Person) -> np.ndarray | None:
    """HSV-гистограмма (H16×S8) торса — «цвет одежды» человека. None, если торс слишком мал.

    Считается только по прямоугольнику торса, без обработки всего кадра.
    """
    h, w = image.shape[:2]
    region = _torso_mask(p, w, h)
    if region is None:
        return None
    x0, y0, mask = region
    roi = image[y0 : y0 + mask.shape[0], x0 : x0 + mask.shape[1]]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], mask, [16, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 1.0, 0.0, cv2.NORM_L1)
    return hist.flatten().astype(np.float32)


def compute_histograms(image: np.ndarray | None, people: list[Person]) -> list[np.ndarray | None]:
    """Гистограммы всех найденных людей. Вызывается до блокировки трекера (это самая дорогая часть)."""
    if image is None:
        return [None] * len(people)
    return [torso_histogram(image, p) for p in people]


def appearance_distance(a: np.ndarray, b: np.ndarray) -> float:
    """0 — одинаковая одежда, 1 — совсем разная (расстояние Бхаттачарии)."""
    return float(cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA))


@dataclass(eq=False)  # треки сравниваются по номеру, не по содержимому (там массивы numpy)
class Track:
    id: int
    box: tuple[float, float, float, float]
    score: float
    keypoints: np.ndarray
    last_seen: float
    hits: int = 1
    misses: int = 0
    velocity: tuple[float, float] = (0.0, 0.0)  # px/с центра рамки
    appearance: np.ndarray | None = None
    created: float = 0.0

    def predicted_box(self, t: float) -> tuple[float, float, float, float]:
        dt = min(max(0.0, t - self.last_seen), MAX_EXTRAPOLATION_S)
        dx, dy = self.velocity[0] * dt, self.velocity[1] * dt
        x1, y1, x2, y2 = self.box
        return (x1 + dx, y1 + dy, x2 + dx, y2 + dy)


class PersonTracker:
    def __init__(
        self,
        match_iou: float = 0.3,
        max_missing_s: float = 2.0,
        reid_max_distance: float = 0.35,
        appearance_alpha: float = 0.1,
        new_track_score: float = 0.25,
        reid_hard_distance: float = 0.6,
    ):
        self.match_iou = match_iou
        self.max_missing_s = max_missing_s
        self.reid_max_distance = reid_max_distance
        self.appearance_alpha = appearance_alpha
        self.new_track_score = new_track_score
        # Внешность одного и того же человека «шумит» от кадра к кадру (с площадки: 0.12–0.37),
        # поэтому единственного кандидата на месте цели отвергаем, только если он явно чужой.
        self.reid_hard_distance = reid_hard_distance
        self._tracks: dict[int, Track] = {}
        self._ids = itertools.count(1)
        self._t: float | None = None
        self.target_id: int | None = None
        self._target_app: np.ndarray | None = None
        self._target_lost_since: float | None = None

    # --- состояние ------------------------------------------------------

    @property
    def tracks(self) -> list[Track]:
        return list(self._tracks.values())

    @property
    def target(self) -> Track | None:
        return self._tracks.get(self.target_id) if self.target_id is not None else None

    @property
    def target_state(self) -> Literal["none", "tracking", "lost"]:
        if self.target_id is None:
            return "none"
        tr = self.target
        return "tracking" if tr is not None and tr.last_seen == self._t else "lost"

    # --- выбор цели -----------------------------------------------------

    def select_at(self, x: float, y: float) -> int | None:
        hits = [
            t for t in self._tracks.values()
            if t.last_seen == self._t and t.box[0] <= x <= t.box[2] and t.box[1] <= y <= t.box[3]
        ]
        if not hits:
            return None
        best = min(hits, key=lambda t: _area(t.box))
        self.select(best.id)
        return best.id

    def select(self, track_id: int) -> bool:
        tr = self._tracks.get(track_id)
        if tr is None:
            return False
        self.target_id = tr.id
        self._target_app = None if tr.appearance is None else tr.appearance.copy()
        self._target_lost_since = None
        return True

    def clear_target(self) -> None:
        self.target_id = None
        self._target_app = None
        self._target_lost_since = None

    # --- обновление -----------------------------------------------------

    def _target_distance(self, hist: np.ndarray | None) -> float | None:
        if self._target_app is None or hist is None:
            return None
        return appearance_distance(self._target_app, hist)

    def _match_target(self, t: float, people: list[Person], hists) -> int | None:
        """Подобрать детекцию для цели первой, чтобы её не перехватил сосед или «двойник».

        Один кандидат по положению — берём, если он не явно чужой. Несколько — выбираем по внешности.
        """
        tr = self.target
        if tr is None:
            return None
        pb = tr.predicted_box(t)
        cands = []
        for di, p in enumerate(people):
            iou = _iou(pb, p.box)
            if iou >= self.match_iou:
                d = self._target_distance(hists[di])
                if d is None or d <= self.reid_hard_distance:
                    cands.append((d if d is not None else 0.0, -iou, di))
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0][2]
        return min(cands)[2]  # ближе по внешности, при равенстве — больше перекрытие

    def update(
        self,
        people: list[Person],
        t: float,
        image: np.ndarray | None,
        hists: list[np.ndarray | None] | None = None,
    ) -> list[Track]:
        self._t = t
        if hists is None:
            hists = compute_histograms(image, people)
        tracks = list(self._tracks.values())
        used_t, used_d = set(), set()

        tdi = self._match_target(t, people, hists)
        if tdi is not None:
            ti = next(i for i, x in enumerate(tracks) if x.id == self.target_id)
            used_t.add(ti)
            used_d.add(tdi)
            self._apply(tracks[ti], people[tdi], hists[tdi], t, isolated=self._isolated(tdi, people))

        pairs = []
        for ti, tr in enumerate(tracks):
            if tr.id == self.target_id:
                continue
            pb = tr.predicted_box(t)
            for di, p in enumerate(people):
                iou = _iou(pb, p.box)
                if iou >= self.match_iou:
                    pairs.append((iou, ti, di))
        pairs.sort(reverse=True)
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            tr = tracks[ti]
            used_t.add(ti)
            used_d.add(di)
            self._apply(tr, people[di], hists[di], t, isolated=self._isolated(di, people))

        for ti, tr in enumerate(tracks):
            if ti not in used_t:
                tr.misses += 1

        new_ids = []
        for di, p in enumerate(people):
            if di in used_d or p.score < self.new_track_score:
                continue
            tr = Track(
                id=next(self._ids), box=p.box, score=p.score, keypoints=p.keypoints,
                last_seen=t, appearance=hists[di], created=t,
            )
            self._tracks[tr.id] = tr
            new_ids.append(tr.id)

        for tr in list(self._tracks.values()):
            if t - tr.last_seen > self.max_missing_s:
                del self._tracks[tr.id]

        self._update_target(t)
        return [tr for tr in self._tracks.values() if tr.last_seen == t]

    def _isolated(self, di: int, people: list[Person]) -> bool:
        return all(_iou(people[di].box, q.box) < ISOLATION_IOU for j, q in enumerate(people) if j != di)

    def _apply(self, tr: Track, p: Person, hist: np.ndarray | None, t: float, isolated: bool) -> None:
        dt = t - tr.last_seen
        if dt > 0:
            dt = max(dt, MIN_VELOCITY_DT)
            ocx, ocy = (tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2
            ncx, ncy = (p.box[0] + p.box[2]) / 2, (p.box[1] + p.box[3]) / 2
            vx = max(-MAX_SPEED_PX_S, min(MAX_SPEED_PX_S, (ncx - ocx) / dt))
            vy = max(-MAX_SPEED_PX_S, min(MAX_SPEED_PX_S, (ncy - ocy) / dt))
            a = VELOCITY_ALPHA
            tr.velocity = (a * vx + (1 - a) * tr.velocity[0], a * vy + (1 - a) * tr.velocity[1])
        tr.box, tr.score, tr.keypoints = p.box, p.score, p.keypoints
        tr.last_seen, tr.hits, tr.misses = t, tr.hits + 1, 0
        if hist is not None and isolated:
            if tr.appearance is None:
                tr.appearance = hist
            else:
                k = self.appearance_alpha
                tr.appearance = ((1 - k) * tr.appearance + k * hist).astype(np.float32)
            if tr.id == self.target_id:
                self._target_app = tr.appearance.copy()

    def _update_target(self, t: float) -> None:
        if self.target_id is None:
            return
        if self.target_state == "tracking":
            self._target_lost_since = None
            return
        if self._target_lost_since is None:
            self._target_lost_since = t
        if self._target_app is None:
            return
        # узнать цель среди появившихся после её потери
        candidates = [
            tr for tr in self._tracks.values()
            if tr.id != self.target_id and tr.last_seen == t and tr.created >= self._target_lost_since
            and tr.appearance is not None
        ]
        scored = [(appearance_distance(self._target_app, tr.appearance), tr) for tr in candidates]
        scored = [s for s in scored if s[0] <= self.reid_max_distance]
        if scored:
            _, best = min(scored, key=lambda s: s[0])
            # узнанный трек получает номер цели — для оператора это тот же человек
            self._tracks.pop(self.target_id, None)
            self._tracks.pop(best.id)
            best.id = self.target_id
            self._tracks[best.id] = best
            self._target_lost_since = None
