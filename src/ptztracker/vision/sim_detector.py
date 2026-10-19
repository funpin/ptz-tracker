"""«Детектор» для симулятора: берёт точную разметку из кадра. Нужен, чтобы проверять логику без нейросети."""

from __future__ import annotations

import numpy as np

from ptztracker.video.frames import Frame
from ptztracker.vision.types import Person

PAD_X, PAD_Y = 0.15, 0.10  # поля рамки относительно высоты видимой фигуры


class SimDetector:
    name = "Симулятор"
    device = "—"

    def detect(self, frame: Frame) -> list[Person]:
        people = (frame.meta or {}).get("people") or []
        h, w = frame.image.shape[:2]
        out = []
        for kp in people:
            vis = kp[kp[:, 2] > 0]
            if len(vis) < 2:
                continue
            y0, y1 = float(vis[:, 1].min()), float(vis[:, 1].max())
            x0, x1 = float(vis[:, 0].min()), float(vis[:, 0].max())
            span = max(y1 - y0, 1.0)
            box = (
                max(0.0, x0 - PAD_X * span),
                max(0.0, y0 - PAD_Y * span),
                min(float(w), x1 + PAD_X * span),
                min(float(h), y1 + PAD_Y * span),
            )
            if box[2] - box[0] < 2 or box[3] - box[1] < 2:
                continue
            out.append(Person(box=box, score=0.95, keypoints=np.array(kp, np.float32)))
        return out
