from ptztracker.camera.protocol import parse_pan_tilt, parse_zoom
from ptztracker.sim.ptz_model import SimPtz


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def run(s, seconds, dt=0.01):
    for _ in range(int(round(seconds / dt))):
        s.update(dt)


def test_pan_right_moves_right():
    s = SimPtz()
    s.handle("#PTS9950")
    run(s, 1.0)
    assert s.pan_deg > 50


def test_tilt_up_moves_up():
    s = SimPtz()
    s.handle("#PTS5099")
    run(s, 0.5)
    assert s.tilt_deg > 10


def test_stop_decelerates_smoothly():
    s = SimPtz()
    s.handle("#PTS9950")
    run(s, 1.0)
    s.handle("#PTS5050")
    run(s, 0.6)
    p = s.pan_deg
    s.update(0.01)
    assert abs(s.pan_deg - p) < 0.01


def test_no_instant_jump_on_start():
    s = SimPtz()
    s.handle("#PTS9950")
    s.update(0.01)
    assert s.pan_vel < 90 * 0.2  # инерция


def test_slower_at_tele_when_speed_with_zoom():
    a, b = SimPtz(), SimPtz()
    a.zoom, b.zoom = 0.0, 1.0
    for s in (a, b):
        s.handle("#PTS7050")
        run(s, 1.0)
    assert b.pan_deg < a.pan_deg / 10


def test_pan_limited():
    s = SimPtz()
    s.handle("#PTS9950")
    run(s, 5.0)
    assert s.pan_deg <= 175.0


def test_zoom_speed_and_query():
    s = SimPtz()
    assert parse_zoom(s.handle("#GZ")) == 0x555
    s.handle("#Z99")
    run(s, 1.0)
    s.handle("#Z50")
    assert parse_zoom(s.handle("#GZ")) > 0x555


def test_hfov_range():
    s = SimPtz()
    s.zoom = 0.0
    assert abs(s.hfov_deg() - 62.1) < 0.01
    s.zoom = 1.0
    assert abs(s.hfov_deg() - 3.3) < 0.01


def test_apc_query_roundtrip():
    s = SimPtz()
    assert s.handle("#APC") == "aPC80008000"
    s.handle("#APC90008000")
    run(s, 5.0)
    assert abs(parse_pan_tilt(s.handle("#APC"))[0] - 0x9000) <= 2


def test_preset_store_and_recall():
    s = SimPtz()
    s.pan_deg = 20.0
    s.handle("#M03")
    s.pan_deg = -10.0
    s.handle("#R03")
    run(s, 3.0)
    assert abs(s.pan_deg - 20.0) < 0.1


def test_too_fast_gives_er2():
    c = FakeClock()
    s = SimPtz(min_interval=0.13, clock=c)
    s.handle("#O")
    c.t += 0.05
    assert s.handle("#O").lower().startswith("er2")
    c.t += 0.13
    assert s.handle("#O") == "p1"


def test_unknown():
    assert SimPtz().handle("#XYZ").lower().startswith("er1")


def test_model_and_power():
    s = SimPtz()
    assert s.handle("QID") == "OID:AW-HE42"
    assert s.handle("#O") == "p1"


def test_received_log_capped():
    s = SimPtz()
    for _ in range(250):
        s.handle("#O")
    assert len(s.received) == 200
