"""Одноразовый экспорт модели позы YOLO11n-pose в ONNX (в программу не входит).

Запуск во временном окружении:
    python3 -m venv /tmp/export-venv
    /tmp/export-venv/bin/pip install "ultralytics==8.3.*" onnx onnxslim
    /tmp/export-venv/bin/python tools/export_pose_model.py

Результат: src/ptztracker/models/yolo11n-pose.onnx и тестовое фото tests/fixtures/people.jpg.
Лицензия модели — AGPL-3.0 (Ultralytics); программа используется внутри организации.
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    import ultralytics
    from ultralytics import YOLO

    work = Path(tempfile.mkdtemp(prefix="pose-export-"))
    model = YOLO(str(work / "yolo11n-pose.pt"))  # скачает веса в work
    onnx_path = Path(model.export(format="onnx", imgsz=640, opset=17, simplify=True, dynamic=False))
    dst = ROOT / "src" / "ptztracker" / "models" / "yolo11n-pose.onnx"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(onnx_path, dst)
    bus = Path(ultralytics.__file__).parent / "assets" / "bus.jpg"
    fixtures = ROOT / "tests" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bus, fixtures / "people.jpg")
    print(f"Модель: {dst} ({dst.stat().st_size / 1e6:.1f} МБ)")
    print(f"Фото для тестов: {fixtures / 'people.jpg'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
