import time

import numpy as np

from ptztracker.sim.frame_source import SimFrameSource
from ptztracker.sim.ptz_model import SimPtz
from ptztracker.sim.scene import SyntheticStage
from ptztracker.video.frames import Frame
from ptztracker.vision.sim_detector import SimDetector
from ptztracker.vision.types import KP_L_ANKLE, KP_L_HIP, KP_L_SHOULDER, KP_NOSE


def grab(sim):
    fs = SimFrameSource(sim, SyntheticStage(), latency_s=0.0)
    fs.start()
    try:
        time.sleep(0.3)
        return fs.latest()
    finally:
        fs.stop()


def test_sim_frame_has_truth_and_detector_finds_person():
    sim = SimPtz()
    sim.tilt_deg = -8.0
    fr = grab(sim)
    ppl = SimDetector().detect(fr)
    assert len(ppl) == 1
    kp = ppl[0].keypoints
    assert kp[KP_L_SHOULDER, 1] < kp[KP_L_HIP, 1] < kp[KP_L_ANKLE, 1]
    x1, y1, x2, y2 = ppl[0].box
    assert x1 < kp[KP_NOSE, 0] < x2 and y1 < kp[KP_NOSE, 1] < y2


def test_sim_person_out_of_view_not_detected():
    sim = SimPtz()
    sim.pan_deg = -50
    assert SimDetector().detect(grab(sim)) == []


def test_frame_meta_default_none():
    assert Frame(np.zeros((2, 2, 3), np.uint8), 0.0, 1).meta is None
