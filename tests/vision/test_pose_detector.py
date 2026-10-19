from pathlib import Path

import cv2
import numpy as np
import pytest

from ptztracker.video.frames import Frame
from ptztracker.vision.pose_detector import PoseDetector, choose_providers, decode_yolo_pose, letterbox
from ptztracker.vision.types import KP_L_SHOULDER, KP_R_SHOULDER

PHOTO = Path(__file__).resolve().parents[1] / "fixtures" / "people.jpg"


@pytest.fixture(scope="module")
def det():
    return PoseDetector()


def test_choose_providers():
    assert choose_providers(["CPUExecutionProvider", "DmlExecutionProvider"]) == [
        "DmlExecutionProvider",
        "CPUExecutionProvider",
    ]
    assert choose_providers(["CPUExecutionProvider"]) == ["CPUExecutionProvider"]
    assert choose_providers(["CoreMLExecutionProvider", "CPUExecutionProvider"]) == ["CPUExecutionProvider"]


def test_letterbox_shape_and_scale():
    t, s, (px, py) = letterbox(np.zeros((720, 1280, 3), np.uint8))
    assert t.shape == (1, 3, 640, 640) and t.dtype == np.float32
    assert abs(s - 0.5) < 1e-6 and (px, py) == (0, 140)


def test_decode_maps_back_to_frame():
    out = np.zeros((1, 56, 3), np.float32)
    out[0, :5, 0] = [320, 320, 100, 200, 0.9]
    out[0, 5 + 5 * 3 : 5 + 5 * 3 + 3, 0] = [300, 260, 0.9]
    p = decode_yolo_pose(out, scale=0.5, pad=(0, 140))
    assert len(p) == 1
    x1, y1, x2, y2 = p[0].box
    assert (round(x1), round(y1), round(x2), round(y2)) == (540, 160, 740, 560)
    assert tuple(np.round(p[0].keypoints[5, :2])) == (600, 240)


def test_decode_nms_and_threshold():
    out = np.zeros((1, 56, 3), np.float32)
    out[0, :5, 0] = [320, 320, 100, 200, 0.9]
    out[0, :5, 1] = [322, 321, 100, 200, 0.8]
    out[0, :5, 2] = [100, 100, 50, 50, 0.2]
    p = decode_yolo_pose(out, 1.0, (0, 0))
    assert len(p) == 1 and abs(p[0].score - 0.9) < 1e-6


def test_detects_people_on_photo(det):
    img = cv2.imread(str(PHOTO))
    ppl = det.detect(Frame(img, 0.0, 1))
    assert len(ppl) >= 3 and all(p.keypoints.shape == (17, 3) for p in ppl)
    h, w = img.shape[:2]
    assert all(0 <= p.box[0] < p.box[2] <= w + 1 and 0 <= p.box[1] < p.box[3] <= h + 1 for p in ppl)


def test_detects_person_cut_at_waist(det):
    # рабочая крупность: человек обрезан по пояс и занимает почти весь кадр
    img = cv2.imread(str(PHOTO))
    ppl = det.detect(Frame(img, 0.0, 1))
    x1, y1, x2, y2 = map(int, max(ppl, key=lambda p: (p.box[2] - p.box[0]) * (p.box[3] - p.box[1])).box)
    mid = y1 + int((y2 - y1) * 0.55)
    crop = img[max(0, y1 - 20) : mid, max(0, x1 - 60) : min(img.shape[1], x2 + 60)]
    crop = cv2.resize(crop, None, fx=2.5, fy=2.5)
    got = det.detect(Frame(crop, 0.0, 1))
    assert got
    best = got[0]
    assert best.keypoints[KP_L_SHOULDER, 2] > 0.3 and best.keypoints[KP_R_SHOULDER, 2] > 0.3


def test_no_people_on_blank(det):
    assert det.detect(Frame(np.zeros((720, 1280, 3), np.uint8), 0.0, 1)) == []


def test_falls_back_to_cpu_when_dml_fails(monkeypatch):
    import onnxruntime as ort

    real = ort.InferenceSession

    def fake(path, providers=None, **kw):
        if providers and "DmlExecutionProvider" in providers:
            raise RuntimeError("DirectML недоступен")
        return real(path, providers=providers, **kw)

    monkeypatch.setattr(ort, "InferenceSession", fake)
    d = PoseDetector(providers=["DmlExecutionProvider", "CPUExecutionProvider"])
    assert d.device == "CPU"


def test_missing_model_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="модел"):
        PoseDetector(model_path=tmp_path / "nope.onnx")
