"""Нейросетевой детектор людей с ключевыми точками (YOLO11n-pose в ONNX).

На Windows работает на видеокарте через DirectML, если не получается — на процессоре.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from ptztracker.models import POSE_MODEL_PATH
from ptztracker.video.frames import Frame
from ptztracker.vision.types import NUM_KP, Person

log = logging.getLogger(__name__)

INPUT_SIZE = 640
CPU_THREADS = 3  # рядом работают vMix/OBS — не занимать все ядра
PAD_VALUE = 114
_PREFERRED = ("DmlExecutionProvider", "CPUExecutionProvider")
_DEVICE_NAMES = {"DmlExecutionProvider": "DirectML", "CPUExecutionProvider": "CPU"}


def choose_providers(available: list[str]) -> list[str]:
    """DirectML, если есть, затем CPU (CPU всегда в конце)."""
    chosen = [p for p in _PREFERRED if p in available and p != "CPUExecutionProvider"]
    return chosen + ["CPUExecutionProvider"]


def letterbox(img: np.ndarray, size: int = INPUT_SIZE) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Вписать кадр в квадрат size×size с полями. Возвращает (тензор 1×3×S×S RGB 0..1, масштаб, (pad_x, pad_y))."""
    h, w = img.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = round(w * scale), round(h * scale)
    px, py = (size - nw) // 2, (size - nh) // 2
    canvas = np.full((size, size, 3), PAD_VALUE, np.uint8)
    canvas[py : py + nh, px : px + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    t = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return np.ascontiguousarray(t), scale, (px, py)


def decode_yolo_pose(
    out: np.ndarray, scale: float, pad: tuple[int, int], conf: float = 0.35, iou: float = 0.5
) -> list[Person]:
    """Выход модели (1, 56, N) → люди в координатах исходного кадра, по убыванию уверенности."""
    pred = out[0].T  # (N, 56)
    pred = pred[pred[:, 4] >= conf]
    if not len(pred):
        return []
    cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
    boxes_xywh = np.stack([cx - w / 2, cy - h / 2, w, h], axis=1)
    keep = cv2.dnn.NMSBoxes(boxes_xywh.tolist(), pred[:, 4].tolist(), conf, iou)
    keep = np.array(keep).reshape(-1)
    px, py = pad
    people = []
    for i in sorted(keep, key=lambda k: -pred[k, 4]):
        x1, y1, bw, bh = boxes_xywh[i]
        box = ((x1 - px) / scale, (y1 - py) / scale, (x1 + bw - px) / scale, (y1 + bh - py) / scale)
        kp = pred[i, 5 : 5 + NUM_KP * 3].reshape(NUM_KP, 3).copy()
        kp[:, 0] = (kp[:, 0] - px) / scale
        kp[:, 1] = (kp[:, 1] - py) / scale
        people.append(Person(box=tuple(float(v) for v in box), score=float(pred[i, 4]), keypoints=kp))
    return people


class PoseDetector:
    name = "Нейросеть"

    def __init__(
        self,
        model_path: Path = POSE_MODEL_PATH,
        conf: float = 0.10,  # слабые детекции нужны трекеру, чтобы не терять цель за кафедрой
        iou: float = 0.5,
        providers: list[str] | None = None,
    ):
        import onnxruntime as ort

        self.conf, self.iou = conf, iou
        if not Path(model_path).is_file():
            raise RuntimeError(f"Не найден файл модели распознавания: {model_path}")
        wanted = providers or choose_providers(ort.get_available_providers())
        try:
            self._session = self._open(ort, model_path, wanted)
        except Exception as e:
            if wanted == ["CPUExecutionProvider"]:
                raise RuntimeError(f"Не удалось загрузить модель распознавания: {e}") from e
            log.warning("Видеокарта (%s) недоступна для распознавания: %s — перехожу на CPU", wanted, e)
            try:
                self._session = self._open(ort, model_path, ["CPUExecutionProvider"])
            except Exception as e2:
                raise RuntimeError(f"Не удалось загрузить модель распознавания: {e2}") from e2
        used = self._session.get_providers()[0]
        self.device = _DEVICE_NAMES.get(used, used)
        self._input = self._session.get_inputs()[0].name
        log.info("Модель распознавания загружена, устройство: %s", self.device)

    @staticmethod
    def _open(ort, model_path: Path, providers: list[str]):
        """Создать сессию и сразу прогнать пустой кадр: сбои DirectML часто проявляются только при запуске."""
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = CPU_THREADS
        opts.inter_op_num_threads = 1
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        sess = ort.InferenceSession(str(model_path), sess_options=opts, providers=providers)
        sess.run(None, {sess.get_inputs()[0].name: np.zeros((1, 3, INPUT_SIZE, INPUT_SIZE), np.float32)})
        return sess

    def detect(self, frame: Frame) -> list[Person]:
        tensor, scale, pad = letterbox(frame.image)
        out = self._session.run(None, {self._input: tensor})[0]
        return decode_yolo_pose(out, scale, pad, self.conf, self.iou)
