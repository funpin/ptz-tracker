"""Файлы моделей, которые поставляются вместе с программой."""

from pathlib import Path

# YOLO11n-pose (Ultralytics, AGPL-3.0), вход 1×3×640×640, выход 1×56×8400
POSE_MODEL_PATH = Path(__file__).with_name("yolo11n-pose.onnx")
