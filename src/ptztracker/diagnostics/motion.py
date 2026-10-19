"""Измерение движения картинки: на сколько пикселей сдвинулся кадр и когда началось движение."""

from __future__ import annotations

import math
import time

import cv2
import numpy as np

from ptztracker.video.frames import FrameSource

_windows: dict[tuple[int, int], np.ndarray] = {}


def _prep(img: np.ndarray, work_width: int) -> np.ndarray:
    h, w = img.shape[:2]
    if w != work_width:
        img = cv2.resize(img, (work_width, max(1, round(h * work_width / w))), interpolation=cv2.INTER_AREA)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return np.float32(img)


def measure_shift(a: np.ndarray, b: np.ndarray, work_width: int = 480) -> tuple[float, float]:
    """Сдвиг кадра b относительно a в пикселях исходного размера (фазовая корреляция)."""
    ga, gb = _prep(a, work_width), _prep(b, work_width)
    key = (ga.shape[1], ga.shape[0])
    win = _windows.get(key)
    if win is None:
        win = _windows[key] = cv2.createHanningWindow(key, cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(ga, gb, win)
    k = a.shape[1] / work_width
    return dx * k, dy * k


def wait_for_motion(
    source: FrameSource,
    since: float,
    threshold_px: float = 1.5,
    timeout: float = 3.0,
    reference: np.ndarray | None = None,
) -> float | None:
    """Ждать первого кадра, сдвинутого относительно опорного больше порога.

    Возвращает момент (time.monotonic), когда такой кадр стал доступен программе,
    то есть «команда → видимое движение» включает и задержку видеопотока.
    """
    if reference is None:
        fr = source.latest()
        if fr is None:
            return None
        reference = fr.image
    last_seq = -1
    deadline = since + timeout
    while time.monotonic() < deadline:
        fr = source.latest()
        if fr is not None and fr.seq != last_seq:
            last_seq = fr.seq
            dx, dy = measure_shift(reference, fr.image)
            if math.hypot(dx, dy) > threshold_px:
                return time.monotonic()
        time.sleep(0.005)
    return None


def estimate_scale(a: np.ndarray, b: np.ndarray) -> float | None:
    """Во сколько раз увеличилась картинка b относительно a (по особым точкам ORB)."""
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY) if a.ndim == 3 else a
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY) if b.ndim == 3 else b
    orb = cv2.ORB_create(1500)
    ka, da = orb.detectAndCompute(ga, None)
    kb, db = orb.detectAndCompute(gb, None)
    if da is None or db is None or len(ka) < 10 or len(kb) < 10:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    if len(matches) < 10:
        return None
    pa = np.float32([ka[m.queryIdx].pt for m in matches])
    pb = np.float32([kb[m.trainIdx].pt for m in matches])
    m, _ = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC)
    if m is None:
        return None
    return float(math.hypot(m[0, 0], m[1, 0]))
