import time

import numpy as np
import pytest

from ptztracker.camera.session import CameraSession
from ptztracker.config import CameraConfig
from ptztracker.control.camera_model import default_calibration

pytestmark = pytest.mark.slow  # реальное время на симуляторе


def wait_until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def make(cam_id, **cfg):
    s = CameraSession(CameraConfig(id=cam_id, name=cam_id, ip="sim", kind="sim", **cfg))
    s.start()
    return s


def test_two_cameras_track_and_manual():
    """Две независимые сессии с анализом: первая следит за докладчиком, на второй одновременно ручной поворот.
    Распределение анализа (max_analyzed) здесь не участвует — его проверяют tests/multicam/test_analysis.py и окно."""
    from ptztracker.sim.scene import SyntheticStage

    a = CameraSession(
        CameraConfig(
            id="a", name="A", ip="sim", kind="sim", composition="full", calibration=default_calibration().to_json()
        ),
        scene=SyntheticStage(script="lecture"),
    )
    b = make("b")
    a.start()
    sessions = (a, b)
    try:
        for s in sessions:
            assert wait_until(lambda s=s: s.analyzer.latest() is not None and len(s.analyzer.latest().tracks) > 0, 4)
        tr = a.analyzer.latest().tracks[0]
        assert a.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2) is not None
        assert wait_until(lambda: a.analyzer.latest().target_state == "tracking", 2)
        assert a.follower.enable()

        pan0 = b.sim.pan_deg
        errs = []
        t_end = time.monotonic() + 10
        t_manual = time.monotonic() + 1
        manual_on = False
        while time.monotonic() < t_end:
            now = time.monotonic()
            if not manual_on and now >= t_manual:
                b.set_pan_tilt_velocity(0.5, 0)
                manual_on = True
            fr = a.frames.latest()
            kp = fr.meta["people"][0]
            if now > t_end - 5 and kp[0, 2] > 0:
                errs.append(abs(kp[0, 0] / fr.image.shape[1] - 0.5))
            time.sleep(0.1)
        b.set_pan_tilt_velocity(0, 0)

        assert a.follower.enabled, f"слежение первой выключилось: {a.follower.last_reason}"
        assert abs(b.sim.pan_deg - pan0) > 1.0, "вторая камера не повернулась"
        assert errs and np.mean(errs) < 0.10, f"средняя ошибка {np.mean(errs):.3f}"
    finally:
        for s in sessions:
            s.stop()
