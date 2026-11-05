from ptztracker.multicam.velocity import VelocityMixer


class Target:
    def __init__(self):
        self.calls = []

    def set_pan_tilt_velocity(self, vx, vy):
        self.calls.append(("pt", vx, vy))

    def set_zoom_velocity(self, v):
        self.calls.append(("z", v))

    def set_focus_velocity(self, v):
        self.calls.append(("f", v))

    def recall_preset(self, n):
        return ("preset", n)


def test_two_sources_sum():
    t = Target()
    m = VelocityMixer(t)
    m.set("pad1", pan=0.5)
    m.set("pad3", tilt=0.5)
    assert t.calls[-1] == ("pt", 0.5, 0.5)


def test_clamped():
    t = Target()
    m = VelocityMixer(t)
    m.set("a", pan=0.8)
    m.set("b", pan=0.8)
    assert t.calls[-1] == ("pt", 1.0, 0.0)


def test_release_one_keeps_other():
    t = Target()
    m = VelocityMixer(t)
    m.set("pad1", pan=0.5)
    m.set("pad3", tilt=0.5)
    m.release("pad1")
    assert t.calls[-1] == ("pt", 0.0, 0.5)


def test_no_resend_when_sum_same():
    t = Target()
    m = VelocityMixer(t)
    m.set("a", pan=0.5)
    n = len(t.calls)
    m.set("a", pan=0.5)
    m.set("b")
    assert len(t.calls) == n


def test_clear_all_latches_until_zero():
    t = Target()
    m = VelocityMixer(t)
    m.set("pad1", pan=0.5)
    n = len(t.calls)
    m.clear_all()
    assert len(t.calls) == n
    m.set("pad1", pan=1)
    assert len(t.calls) == n
    m.set("pad1")
    m.set("pad1", pan=1)
    assert t.calls[-1] == ("pt", 1.0, 0.0)


def test_source_forwards_other_methods():
    t = Target()
    m = VelocityMixer(t)
    s = m.source("pad1")
    assert s.recall_preset(3) == ("preset", 3)
    s.set_zoom_velocity(0.4)
    assert t.calls[-1] == ("z", 0.4)
    s.set_pan_tilt_velocity(0.2, -0.1)
    assert t.calls[-1] == ("pt", 0.2, -0.1)


def test_clear_all_does_not_latch_idle_source():
    t = Target()
    m = VelocityMixer(t)
    m.set("idle")  # нули
    m.set("held", pan=0.5)
    m.clear_all()
    m.set("idle", pan=1)
    assert t.calls[-1] == ("pt", 1.0, 0.0)


def test_release_clears_latch():
    t = Target()
    m = VelocityMixer(t)
    m.set("pad1", pan=0.5)
    m.clear_all()
    m.release("pad1")
    m.set("pad1", pan=1)
    assert t.calls[-1] == ("pt", 1.0, 0.0)


def test_pt_scale_only_pan_tilt_and_refresh():
    t = Target()
    m = VelocityMixer(t)
    k = [0.5]
    m.pt_scale = lambda: k[0]
    m.set("pad1", pan=0.8, tilt=-0.4, zoom=0.6, focus=0.2)
    assert ("pt", 0.4, -0.2) in t.calls and ("z", 0.6) in t.calls and ("f", 0.2) in t.calls
    n = len(t.calls)
    m.refresh()  # масштаб тот же — ничего не шлём
    assert len(t.calls) == n
    k[0] = 0.25  # зум ушёл в теле, стик держат
    m.refresh()
    assert t.calls[-1] == ("pt", 0.2, -0.1)
    m.set("pad1")
    assert [c for c in t.calls if c[0] == "pt"][-1] == ("pt", 0.0, 0.0)
    k[0] = 1.0
    n = len(t.calls)
    m.refresh()  # стоит — ничего не шлём
    assert len(t.calls) == n


def test_pt_scale_failure_means_no_slowdown():
    t = Target()
    m = VelocityMixer(t)
    m.pt_scale = lambda: 1 / 0
    m.set("a", pan=0.5)
    assert t.calls[-1] == ("pt", 0.5, 0.0)
