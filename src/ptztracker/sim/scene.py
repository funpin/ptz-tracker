"""Сцены для виртуальной камеры: синтетическая сцена с докладчиком или зацикленное видео.

Сцена — это «панорама» всего, что может увидеть камера. Виртуальная камера
вырезает из неё окно по текущему положению и зуму (render).
"""

from __future__ import annotations

import math
import threading

import cv2
import numpy as np

# Сценарий движения докладчика (секунды): стоит у кафедры, идёт вправо, стоит, идёт влево.
_STAND_A, _WALK_R, _STAND_B, _WALK_L = 6.0, 4.0, 5.0, 6.0
_CYCLE = _STAND_A + _WALK_R + _STAND_B + _WALK_L


def _build_pyramid(img: np.ndarray, levels: int = 4) -> list[np.ndarray]:
    pyr = [img]
    for _ in range(levels - 1):
        pyr.append(cv2.pyrDown(pyr[-1]))
    return pyr


def warp_view(
    pyramid: list[np.ndarray],
    cx: float,
    cy: float,
    crop_w: float,
    out_size: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Вырезать окно ширины crop_w с центром (cx, cy) в координатах уровня 0 и вписать в out_size.

    Возвращает (кадр, матрица 2×3 «координаты сцены → координаты кадра»).
    Всё за пределами сцены — чёрное.
    """
    out_w, out_h = out_size
    scale = out_w / crop_w  # сцена → кадр
    level = 0
    while level + 1 < len(pyramid) and scale * (2 ** (level + 1)) <= 1.0:
        level += 1
    src = pyramid[level]
    f = 2**level
    s = scale * f  # уровень → кадр
    m_level = np.array(
        [[s, 0, out_w / 2 - s * cx / f], [0, s, out_h / 2 - s * cy / f]],
        dtype=np.float64,
    )
    img = cv2.warpAffine(src, m_level, (out_w, out_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    m_scene = np.array([[scale, 0, out_w / 2 - scale * cx], [0, scale, out_h / 2 - scale * cy]])
    return img, m_scene


class SyntheticStage:
    """Сцена с фоном, экраном, кафедрой и одним идущим человеком. Полностью детерминирована по seed."""

    def __init__(self, width: int = 7680, height: int = 2560, seed: int = 0):
        self.size = (width, height)
        self.seed = seed
        rng = np.random.default_rng(seed)
        w, h = width, height
        floor_y = int(h * 0.8)
        self.floor_y = floor_y
        img = np.zeros((h, w, 3), np.uint8)
        # стена: вертикальный градиент + шум-текстура (нужна для оценки сдвига кадра)
        grad = np.linspace(40, 90, floor_y, dtype=np.float32)[:, None]
        img[:floor_y] = np.stack([grad * 0.9, grad * 0.8, grad * 0.7], axis=-1).astype(np.uint8)
        small = rng.integers(0, 60, (h // 16 + 1, w // 16 + 1, 3), dtype=np.uint8)
        noise = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        img = cv2.add(img, noise)
        # декоративные панели и светильники
        for _ in range(140):
            x, y = int(rng.integers(0, w)), int(rng.integers(0, floor_y - 50))
            pw, ph = int(rng.integers(30, 260)), int(rng.integers(30, 260))
            color = tuple(int(c) for c in rng.integers(30, 200, 3))
            cv2.rectangle(img, (x, y), (x + pw, y + ph), color, -1)
        # пол
        img[floor_y:] = (35, 45, 60)
        for x in range(0, w, 160):
            cv2.line(img, (x, floor_y), (x - 300, h), (50, 60, 75), 3)
        # экран для слайдов
        sx0, sx1 = int(w * 0.18), int(w * 0.38)
        cv2.rectangle(img, (sx0, int(h * 0.15)), (sx1, int(h * 0.55)), (230, 225, 220), -1)
        cv2.putText(img, "SLIDE", (sx0 + 120, int(h * 0.38)), cv2.FONT_HERSHEY_SIMPLEX, 12, (90, 60, 40), 25)
        # кафедра
        lx = int(w * 0.42)
        cv2.rectangle(img, (lx, floor_y - int(h * 0.12)), (lx + int(w * 0.025), floor_y), (40, 60, 90), -1)
        self._bg = img
        self._pyr = _build_pyramid(img)
        self._person_h = h * 0.18
        self._x0, self._x1 = w * 0.45, w * 0.62
        self._shirt = tuple(int(c) for c in rng.integers(60, 220, 3))
        self._pants = tuple(int(c) for c in rng.integers(20, 90, 3))

    def person_x(self, t: float) -> float:
        u = t % _CYCLE
        if u < _STAND_A:
            return self._x0
        u -= _STAND_A
        if u < _WALK_R:
            return self._x0 + (self._x1 - self._x0) * (u / _WALK_R)
        u -= _WALK_R
        if u < _STAND_B:
            return self._x1
        u -= _STAND_B
        return self._x1 - (self._x1 - self._x0) * (u / _WALK_L)

    def _walking(self, t: float) -> bool:
        u = t % _CYCLE
        return _STAND_A <= u < _STAND_A + _WALK_R or u >= _STAND_A + _WALK_R + _STAND_B

    def _draw_person(self, img: np.ndarray, m: np.ndarray, t: float) -> None:
        s = m[0, 0]
        px = self.person_x(t)

        def P(x, y):  # noqa: N802 — сцена → кадр
            return int(round(m[0, 0] * x + m[0, 2])), int(round(m[1, 1] * y + m[1, 2]))

        hgt = self._person_h
        foot = self.floor_y
        swing = math.sin(t * 6.0) * hgt * 0.08 if self._walking(t) else 0.0
        thick = max(1, int(hgt * 0.07 * s))
        hip = foot - hgt * 0.47
        sh = foot - hgt * 0.82
        # ноги
        cv2.line(img, P(px - hgt * 0.05, hip), P(px - hgt * 0.06 + swing, foot), self._pants, thick)
        cv2.line(img, P(px + hgt * 0.05, hip), P(px + hgt * 0.06 - swing, foot), self._pants, thick)
        # торс
        pts = np.array([P(px - hgt * 0.11, sh), P(px + hgt * 0.11, sh), P(px + hgt * 0.09, hip), P(px - hgt * 0.09, hip)])
        cv2.fillConvexPoly(img, pts, self._shirt)
        # руки
        cv2.line(img, P(px - hgt * 0.11, sh), P(px - hgt * 0.14 - swing * 0.5, hip), self._shirt, max(1, thick // 2 + 1))
        cv2.line(img, P(px + hgt * 0.11, sh), P(px + hgt * 0.14 + swing * 0.5, hip), self._shirt, max(1, thick // 2 + 1))
        # голова
        cv2.circle(img, P(px, foot - hgt * 0.92), max(1, int(hgt * 0.075 * s)), (150, 170, 210), -1)

    def render(self, t: float, cx: float, cy: float, crop_w: float, out_size: tuple[int, int]) -> np.ndarray:
        img, m = warp_view(self._pyr, cx, cy, crop_w, out_size)
        self._draw_person(img, m, t)
        return img

    def frame_at(self, t: float) -> np.ndarray:
        img = self._bg.copy()
        self._draw_person(img, np.array([[1.0, 0, 0], [0, 1.0, 0]]), t)
        return img


class VideoScene:
    """Зацикленное видео (например, запись доклада общим планом) как панорама для виртуальной камеры."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise ValueError(f"Не удалось открыть видео: {path}")
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 25.0
        ok, frame = self._cap.read()
        if not ok:
            raise ValueError(f"В видео нет кадров: {path}")
        self._frame = frame
        self._index = 0
        self.size = (frame.shape[1], frame.shape[0])

    def frame_at(self, t: float) -> np.ndarray:
        want = int(t * self._fps)
        with self._lock:
            while self._index < want:
                ok, frame = self._cap.read()
                if not ok:  # конец — по кругу
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, frame = self._cap.read()
                    if not ok:
                        break
                self._frame = frame
                self._index += 1
            return self._frame

    def render(self, t: float, cx: float, cy: float, crop_w: float, out_size: tuple[int, int]) -> np.ndarray:
        img, _ = warp_view(_build_pyramid(self.frame_at(t)), cx, cy, crop_w, out_size)
        return img
