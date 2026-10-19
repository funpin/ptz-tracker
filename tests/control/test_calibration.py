import time

import numpy as np
import pytest

from ptztracker.camera.client import CameraClient
from ptztracker.control.calibration import CalibrationRunner
from ptztracker.sim.frame_source import SimFrameSource
from ptztracker.sim.http_server import SimHttpServer
from ptztracker.sim.ptz_model import HE42_FW_S, SimPtz
from ptztracker.sim.scene import SyntheticStage

pytestmark = pytest.mark.slow


class FlatScene:
    size = (7680, 2560)

    def render(self, t, cx, cy, crop_w, out_size):
        return np.full((out_size[1], out_size[0], 3), 90, np.uint8)


def make(scene=None):
    sim = SimPtz()
    srv = SimHttpServer(sim)
    srv.start()
    src = SimFrameSource(sim, scene or SyntheticStage(script="stand"), out_size=(640, 360), latency_s=0.15)
    src.start()
    time.sleep(0.5)
    client = CameraClient(srv.url_host)
    return sim, srv, src, client


def test_calibration_recovers_sim_table_and_returns_home():
    sim, srv, src, client = make()
    try:
        sim.pan_deg, sim.tilt_deg = 3.0, -8.0
        r = CalibrationRunner(client.send, src, lambda m: None, zooms=[0x555], codes=(55, 60, 65, 70, 75, 80))
        cal = r.run()
        for code in (60, 65, 70, 75, 80):
            got = cal.pan.speed(code, 0x555)
            assert abs(got / HE42_FW_S[code] - 1) < 0.15, (code, got, HE42_FW_S[code])
        assert 0.2 <= cal.tau <= 0.55
        assert cal.zoom.rate(75) > 0.1
        assert cal.created
        assert abs(sim.pan_deg - 3.0) < 0.5 and abs(sim.tilt_deg + 8.0) < 0.5 and sim.zoom < 0.01
    finally:
        src.stop()
        srv.stop()


def test_flat_scene_reports_error():
    sim, srv, src, client = make(FlatScene())
    try:
        r = CalibrationRunner(client.send, src, lambda m: None, zooms=[0x555], codes=(60, 70, 80))
        with pytest.raises(RuntimeError, match="однотонн"):
            r.run()
    finally:
        src.stop()
        srv.stop()
