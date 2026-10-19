import time

import cv2
import numpy as np

from ptztracker.sim.frame_source import SimFrameSource
from ptztracker.sim.ptz_model import SimPtz
from ptztracker.sim.scene import SyntheticStage


def _gray(img):
    return np.float32(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))


def test_sim_frame_moves_with_pan():
    sim = SimPtz()
    fs = SimFrameSource(sim, SyntheticStage(), latency_s=0.0)
    fs.start()
    try:
        time.sleep(0.3)
        a = fs.latest().image
        sim.pan_deg = 10
        time.sleep(0.3)
        b = fs.latest().image
    finally:
        fs.stop()
    (dx, _), _ = cv2.phaseCorrelate(_gray(a), _gray(b))
    assert dx < -20  # поворот вправо — картинка уезжает влево


def test_sim_frame_size():
    fs = SimFrameSource(SimPtz(), SyntheticStage(), out_size=(640, 360), latency_s=0.0)
    fs.start()
    try:
        time.sleep(0.3)
        assert fs.latest().image.shape == (360, 640, 3)
        assert fs.stats().connected
    finally:
        fs.stop()


def test_sim_latency():
    fs = SimFrameSource(SimPtz(), SyntheticStage(), latency_s=0.3)
    fs.start()
    try:
        time.sleep(0.8)
        fr = fs.latest()
        # кадр «дошёл» только что (t_capture — момент получения), а отрисован на 0.3 с раньше
        assert abs(fr.t_capture - time.monotonic()) < 0.1
        seen = fr.seq
        time.sleep(0.05)
        assert fs.latest().seq >= seen
    finally:
        fs.stop()


def test_out_of_scene_is_black_not_crash():
    sim = SimPtz()
    sim.pan_deg = 170
    fs = SimFrameSource(sim, SyntheticStage(), latency_s=0.0)
    fs.start()
    try:
        time.sleep(0.3)
        assert fs.latest().image.max() == 0
    finally:
        fs.stop()


def test_synthetic_stage_deterministic():
    assert np.array_equal(SyntheticStage(seed=1).frame_at(3.0), SyntheticStage(seed=1).frame_at(3.0))


def test_person_walks():
    st = SyntheticStage()
    assert st.person_x(0.0) != st.person_x(8.0)
