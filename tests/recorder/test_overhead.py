"""Review Focus 1: запись не замедляет анализ (реальное время на симуляторе)."""

import time

import pytest

from ptztracker.camera.session import CameraSession
from ptztracker.config import AppConfig, CameraConfig

pytestmark = pytest.mark.slow

MEASURE_S = 10.0  # на каждый режим; всего ~20 с


def wait_until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def analysis_fps(tmp_path, record: bool) -> float:
    cfg = CameraConfig(id="s", name="Сим", ip="sim", kind="sim")
    s = CameraSession(cfg, app_cfg=AppConfig(record_enabled=record, record_dir=str(tmp_path)))
    s.start()
    try:
        assert s.recorder.active is record
        assert wait_until(lambda: s.analyzer.latest() is not None and len(s.analyzer.latest().tracks) > 0, 4)
        tr = s.analyzer.latest().tracks[0]
        s.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)
        assert wait_until(lambda: s.analyzer.latest().target_state == "tracking", 2)
        assert s.follower.enable()
        seqs = set()
        t0 = time.monotonic()
        while time.monotonic() - t0 < MEASURE_S:
            seqs.add(s.analyzer.latest().seq)
            time.sleep(0.005)
        assert s.follower.enabled, s.follower.last_reason
        return len(seqs) / MEASURE_S
    finally:
        s.stop()


def test_recording_overhead_under_5_percent(tmp_path):
    without = analysis_fps(tmp_path, record=False)
    with_rec = analysis_fps(tmp_path, record=True)
    assert with_rec >= 0.95 * without, f"с записью {with_rec:.1f} к/с, без записи {without:.1f} к/с"
