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
    assert choose_providers(["AzureExecutionProvider", "CPUExecutionProvider"]) == ["CPUExecutionProvider"]


def test_choose_providers_prefers_coreml_on_mac():
    assert choose_providers(["CoreMLExecutionProvider", "CPUExecutionProvider"]) == [
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
    ]


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


class _FakeSess:
    def __init__(self, real, fail_run, provider="DmlExecutionProvider"):
        self.real, self.fail_run, self.provider = real, fail_run, provider

    def run(self, *a, **k):
        if self.fail_run:
            raise RuntimeError(f"{self.provider}: device removed")
        return self.real.run(*a, **k)

    def get_inputs(self):
        return self.real.get_inputs()

    def get_providers(self):
        return [self.provider] if self.fail_run else self.real.get_providers()


def test_falls_back_to_cpu_when_dml_fails_on_first_run(monkeypatch):
    # Final review I3: DirectML создался, но падает при первом распознавании
    import onnxruntime as ort

    real = ort.InferenceSession

    def fake(path, sess_options=None, providers=None, **kw):
        s = real(path, sess_options=sess_options, providers=["CPUExecutionProvider"])
        return _FakeSess(s, fail_run=bool(providers and "DmlExecutionProvider" in providers))

    monkeypatch.setattr(ort, "InferenceSession", fake)
    d = PoseDetector(providers=["DmlExecutionProvider", "CPUExecutionProvider"])
    assert d.device == "CPU"


def test_coreml_failure_falls_back_to_cpu(monkeypatch):
    # CoreML создался, но падает при первом прогоне -> молча на CPU
    import onnxruntime as ort

    real = ort.InferenceSession

    def fake(path, sess_options=None, providers=None, **kw):
        s = real(path, sess_options=sess_options, providers=["CPUExecutionProvider"])
        bad = bool(providers and "CoreMLExecutionProvider" in providers)
        return _FakeSess(s, fail_run=bad, provider="CoreMLExecutionProvider")

    monkeypatch.setattr(ort, "InferenceSession", fake)
    d = PoseDetector(providers=["CoreMLExecutionProvider", "CPUExecutionProvider"])
    assert d.device == "CPU"


def test_cpu_session_limits_threads_and_spinning(monkeypatch):
    # Final review I4: не занимать все ядра рядом с vMix/OBS
    import onnxruntime as ort

    real = ort.InferenceSession
    seen = {}

    def spy(path, sess_options=None, providers=None, **kw):
        seen["opts"] = sess_options
        return real(path, sess_options=sess_options, providers=providers)

    monkeypatch.setattr(ort, "InferenceSession", spy)
    PoseDetector(providers=["CPUExecutionProvider"])
    o = seen["opts"]
    assert o is not None and 1 <= o.intra_op_num_threads <= 3
    assert o.get_session_config_entry("session.intra_op.allow_spinning") == "0"


# --- DirectML: одна нейросеть на все камеры, считает по очереди (вылет на площадке 2026-11-13) ---


class _DmlLikeSess:
    """Сессия на процессоре, которая называет себя DirectML и считает, сколько прогонов идёт одновременно."""

    def __init__(self, real, fail=None):
        import threading

        self.real, self.fail = real, fail or (lambda: False)
        self.active = self.peak = self.runs = 0
        self._count = threading.Lock()

    def run(self, *a, **k):
        import time

        with self._count:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.fail():
                raise RuntimeError("DmlExecutionProvider: device removed")
            time.sleep(0.005)  # окно, в которое влез бы второй поток
            return self.real.run(*a, **k)
        finally:
            with self._count:
                self.active -= 1
                self.runs += 1

    def get_inputs(self):
        return self.real.get_inputs()

    def get_providers(self):
        return ["DmlExecutionProvider", "CPUExecutionProvider"]


@pytest.fixture
def dml(monkeypatch):
    """ort.InferenceSession, у которого запрос DirectML даёт _DmlLikeSess; made — созданные сессии по порядку."""
    import onnxruntime as ort

    from ptztracker.vision import pose_detector

    real, made = ort.InferenceSession, []
    monkeypatch.setattr(pose_detector, "_shared", {})  # общая сессия живёт весь процесс — тесту нужна своя

    def fake(path, sess_options=None, providers=None, **kw):
        if providers and providers[0] == "DmlExecutionProvider":
            s = _DmlLikeSess(real(path, sess_options=sess_options, providers=["CPUExecutionProvider"]))
        else:
            s = real(path, sess_options=sess_options, providers=providers)
        made.append(s)
        return s

    monkeypatch.setattr(ort, "InferenceSession", fake)
    return made


DML = ["DmlExecutionProvider", "CPUExecutionProvider"]


def _blank():
    return Frame(np.zeros((360, 640, 3), np.uint8), 0.0, 0)


def test_directml_cameras_share_one_session(dml):
    """Площадка 2026-11-13, Windows: включили «2 камеры на нейросети» — программа вылетела. Вторая камера создавала
    свою сессию DirectML, пока первая считала. Теперь сессия DirectML в программе одна: вторая камера берёт её же."""
    a, b = PoseDetector(providers=DML), PoseDetector(providers=DML)
    assert len(dml) == 1
    assert a.device == b.device == "DirectML"
    assert a.detect(_blank()) == [] and b.detect(_blank()) == []


def test_directml_runs_one_at_a_time(dml):
    import threading

    a, b = PoseDetector(providers=DML), PoseDetector(providers=DML)
    sess = dml[0]
    errors = []

    def work(d):
        try:
            for _ in range(15):
                d.detect(_blank())
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(d,)) for d in (a, b)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert not errors and sess.runs == 31  # 30 кадров и один пробный прогон при создании
    assert sess.peak == 1  # два потока — ни разу вместе


def test_other_devices_keep_a_session_per_camera(dml):
    """На процессоре и CoreML всё как было: у каждой камеры своя сессия, они считают параллельно."""
    PoseDetector(providers=["CPUExecutionProvider"])
    PoseDetector(providers=["CPUExecutionProvider"])
    assert len(dml) == 2 and dml[0] is not dml[1]


def test_broken_directml_session_is_replaced_on_next_create(dml):
    """Анализатор после серии ошибок пересоздаёт детектор: сломанная общая сессия не должна вернуться к нему же."""
    a = PoseDetector(providers=DML)
    broken = {"on": True}
    dml[0].fail = lambda: broken["on"]
    with pytest.raises(RuntimeError):
        a.detect(_blank())
    b = PoseDetector(providers=DML)
    assert len(dml) == 2 and b.detect(_blank()) == []
