"""Проективная модель виртуальной камеры: поворот pan/tilt вокруг центра, пинхол, бочка объектива.

Мир: камера в начале координат, X — вправо, Y — вниз, Z — вперёд (pan = tilt = 0), метры.
Поворот камеры: сначала pan вокруг вертикали мира, затем tilt вокруг горизонтальной оси камеры
(как у поворотной головы). pan > 0 — вправо, tilt > 0 — вверх (как в SimPtz).

Объектив: f = (W/2) / tan(hfov/2) — угол обзора задан для неискажённого пинхола; дисторсия
x_d = x_u·(1 + k1·r_u²), r в долях f. При k1 < 0 (бочка) края кадра стягиваются к центру.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from ptztracker.camera import he42

NEWTON_STEPS = 6


def focal_px(hfov_deg: float, width: int) -> float:
    """Фокусное расстояние в пикселях для горизонтального угла обзора hfov_deg."""
    return (width / 2) / math.tan(math.radians(hfov_deg) / 2)


def lens_k1(hfov_deg: float) -> float:
    """Коэффициент бочки по углу обзора: he42.LENS_K1_WIDE на широком угле, 0 на теле
    (линейно по ln угла обзора, т. е. по положению зума)."""
    wide, tele = he42.HFOV_WIDE_DEG, he42.HFOV_TELE_DEG
    frac = math.log(max(hfov_deg, tele) / tele) / math.log(wide / tele)
    return he42.LENS_K1_WIDE * min(1.0, max(0.0, frac))


def rotation(pan_deg: float, tilt_deg: float) -> np.ndarray:
    """Матрица 3×3 «камера → мир»."""
    p, t = math.radians(pan_deg), math.radians(tilt_deg)
    cp, sp, ct, st = math.cos(p), math.sin(p), math.cos(t), math.sin(t)
    yaw = np.array([[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]])
    pitch = np.array([[1.0, 0.0, 0.0], [0.0, ct, -st], [0.0, st, ct]])
    return yaw @ pitch


def _r2_limit(k1: float) -> float:
    """Дальше этого r² функция r·(1 + k1·r²) перестаёт расти (складка) — точки там не видны."""
    return 1.0 / (3.0 * -k1) if k1 < 0 else math.inf


def project(
    points: np.ndarray,
    pan_deg: float,
    tilt_deg: float,
    hfov_deg: float,
    out_size: tuple[int, int],
    k1: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Точки мира (N×3) → пиксели кадра (N×2, NaN — за камерой) и флаг «видна в кадре» (N,).

    Пиксель (0, 0) — левый верхний угол; центр пикселя i — i + 0.5 (как у cv2.remap с картой i).
    """
    w, h = out_size
    if k1 is None:
        k1 = lens_k1(hfov_deg)
    f = focal_px(hfov_deg, w)
    pc = np.asarray(points, np.float64) @ rotation(pan_deg, tilt_deg)  # = (Rᵀ·p)ᵀ
    z = pc[:, 2]
    front = z > 1e-6
    zs = np.where(front, z, 1.0)
    xu, yu = pc[:, 0] / zs, pc[:, 1] / zs
    r2 = xu * xu + yu * yu
    ok = front & (r2 < _r2_limit(k1))
    fac = 1.0 + k1 * r2
    xy = np.stack([w / 2 + f * xu * fac, h / 2 + f * yu * fac], axis=1)
    xy[~ok] = np.nan
    vis = ok & (xy[:, 0] >= 0) & (xy[:, 0] < w) & (xy[:, 1] >= 0) & (xy[:, 1] < h)
    return xy, vis


def _undistorted_rays(f: float, k1: float, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Лучи (len(ys)×len(xs)×3, z = 1) через точки кадра xs, ys (px от центра) с учётом дисторсии."""
    xd = (xs / f)[None, :]
    yd = (ys / f)[:, None]
    rd = np.sqrt(xd * xd + yd * yd)
    # обратная дисторсия: решаем r_d = r·(1 + k1·r²) методом Ньютона
    r = rd.copy()
    for _ in range(NEWTON_STEPS):
        r -= (r + k1 * r**3 - rd) / (1.0 + 3.0 * k1 * r * r)
    scale = np.divide(r, rd, out=np.ones_like(rd), where=rd > 0)
    rays = np.empty((len(ys), len(xs), 3), np.float32)
    rays[..., 0] = xd * scale
    rays[..., 1] = yd * scale
    rays[..., 2] = 1.0
    rays.setflags(write=False)
    return rays


@lru_cache(maxsize=8)
def _rays_cached(hfov_deg: float, out_size: tuple[int, int], k1: float, step: int) -> np.ndarray:
    w, h = out_size
    f = focal_px(hfov_deg, w)
    if step == 1:  # центры пикселей
        xs, ys = np.arange(w) + 0.5, np.arange(h) + 0.5
    else:  # узлы сетки с шагом step и одним узлом запаса с каждой стороны (см. grid_rays)
        xs = (np.arange(w // step + 2) - 0.5) * step
        ys = (np.arange(h // step + 2) - 0.5) * step
    return _undistorted_rays(f, k1, xs - w / 2, ys - h / 2)


def pixel_rays(hfov_deg: float, out_size: tuple[int, int], k1: float | None = None) -> np.ndarray:
    """Лучи (H×W×3, в системе камеры, z = 1) через центры пикселей с учётом дисторсии. Кэшируются:
    зависят только от зума и размера кадра, а не от поворота."""
    if k1 is None:
        k1 = lens_k1(hfov_deg)
    return _rays_cached(float(hfov_deg), tuple(out_size), float(k1), 1)


def grid_rays(hfov_deg: float, out_size: tuple[int, int], step: int, k1: float | None = None) -> np.ndarray:
    """Лучи в узлах редкой сетки: узел j — точка кадра (j − 0.5)·step, j = 0 … W/step + 1.

    Карта, посчитанная в узлах и растянутая cv2.resize (INTER_LINEAR) в step раз, после обрезки
    step пикселей с каждого края совпадает с картой по центрам пикселей с точностью до линейной
    интерполяции внутри ячейки (доли пикселя). W и H должны делиться на step.
    """
    if k1 is None:
        k1 = lens_k1(hfov_deg)
    return _rays_cached(float(hfov_deg), tuple(out_size), float(k1), int(step))
