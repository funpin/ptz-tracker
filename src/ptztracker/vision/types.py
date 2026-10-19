"""Общие типы распознавания: найденный человек и интерфейс детектора."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ptztracker.video.frames import Frame

# Ключевые точки COCO-17
KP_NOSE = 0
KP_L_SHOULDER, KP_R_SHOULDER = 5, 6
KP_L_HIP, KP_R_HIP = 11, 12
KP_L_KNEE, KP_R_KNEE = 13, 14
KP_L_ANKLE, KP_R_ANKLE = 15, 16
NUM_KP = 17


@dataclass(frozen=True, eq=False)
class Person:
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 в пикселях кадра
    score: float
    keypoints: np.ndarray  # (17, 3): x, y, уверенность — в пикселях кадра


class Detector(Protocol):
    name: str
    device: str

    def detect(self, frame: Frame) -> list[Person]: ...
