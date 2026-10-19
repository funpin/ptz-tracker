"""Композиции кадра и измерение человека по ключевым точкам.

Размер человека меряется в U — вертикаль «нос → линия плеч»: она почти не меняется,
когда человек поворачивается боком (в отличие от ширины плеч). Тело в единицах U:
макушка −1,0; нос 0; плечи 1,0; середина груди 2,0; пояс 3,7; бёдра 4,3; колени 7,0; ступни 10,3.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ptztracker.vision.types import KP_L_SHOULDER, KP_NOSE, KP_R_SHOULDER

KP_MIN_CONF = 0.3
U_PER_SHOULDER = 0.375  # у взрослого «нос→плечи» ≈ 0.375 ширины плеч (15 см / 40 см)


@dataclass(frozen=True)
class Composition:
    key: str
    title: str
    anchor: tuple[float, float]  # где в кадре должен быть нос (доли ширины и высоты)
    u_frac: float  # желаемая U в долях высоты кадра


COMPOSITIONS: dict[str, Composition] = {
    c.key: c
    for c in (
        Composition("waist_center", "Пояс · центр", (0.50, 0.25), 0.19),
        Composition("waist_left", "Пояс · слева", (0.38, 0.25), 0.19),
        Composition("waist_right", "Пояс · справа", (0.62, 0.25), 0.19),
        Composition("full", "Рост", (0.50, 0.17), 0.075),
        Composition("wide", "Общий", (0.50, 0.40), 0.035),
    )
}
DEFAULT_COMPOSITION = "waist_center"


@dataclass(frozen=True)
class BodyMeasure:
    nose: tuple[float, float]  # доли кадра
    u: float | None  # доли высоты кадра; None — в этом кадре не измерить
    confident: bool


def measure_body(
    keypoints: np.ndarray,
    box: tuple[float, float, float, float],
    frame_size: tuple[int, int],
    last_u: float | None,
) -> BodyMeasure | None:
    w, h = frame_size
    if w <= 0 or h <= 0 or box[2] <= box[0] or box[3] <= box[1]:
        return None
    nose, ls, rs = keypoints[KP_NOSE], keypoints[KP_L_SHOULDER], keypoints[KP_R_SHOULDER]
    has_nose = nose[2] >= KP_MIN_CONF
    has_sh = ls[2] >= KP_MIN_CONF and rs[2] >= KP_MIN_CONF
    if has_sh:
        # Опора — плечи: они не двигаются, когда докладчик смотрит в записи или поворачивает голову.
        sx, sy = (ls[0] + rs[0]) / 2 / w, (ls[1] + rs[1]) / 2 / h
        sw_u = U_PER_SHOULDER * abs(ls[0] - rs[0]) / h  # ширина плеч в единицах U (доли высоты)
        u = None
        if has_nose and sy - nose[1] / h > 0:
            # голову наклонил — «нос→плечи» сжимается, а плечи нет: берём большее
            u = max(sy - nose[1] / h, sw_u)
        ref_u = last_u if last_u is not None else u
        if ref_u is not None:
            return BodyMeasure((sx, sy - ref_u), u, True)
    bx = (box[0] + box[2]) / 2 / w
    top = box[1] / h
    dy = last_u if last_u is not None else 0.08 * (box[3] - box[1]) / h
    return BodyMeasure((bx, top + dy), None, False)
