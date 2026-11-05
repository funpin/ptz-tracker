"""Лента состояний камеры для «Сводки»: дорожка за 10 минут и часы (пользователь 2026-11-05, вариант 2)."""

from ptztracker.multicam.timeline import WINDOW_S, StateTimeline


def test_segments_cover_window_with_states():
    tl = StateTimeline()
    tl.record(0.0, "off", False)
    tl.record(100.0, "on", False)
    tl.record(130.0, "lost", False)
    tl.record(140.0, "on", True)
    segs = tl.segments(200.0)
    assert [s.state for s in segs] == ["off", "on", "lost", "on"]
    assert segs[-1].f1 == 1.0
    assert abs(segs[0].f0 - 0.0) < 1e-9  # от начала ленты (меньше окна прошло)
    air = tl.air(200.0)
    assert len(air) == 1 and air[0].f1 == 1.0


def test_old_samples_fall_out_of_window():
    tl = StateTimeline()
    tl.record(0.0, "on", False)
    tl.record(10.0, "off", False)
    segs = tl.segments(10.0 + WINDOW_S + 5)
    assert [s.state for s in segs] == ["off"] and segs[0].f0 == 0.0


def test_clock_prefers_air_then_tracking():
    tl = StateTimeline()
    tl.record(0.0, "on", False)
    tl.record(50.0, "on", True)
    assert tl.clock(110.0) == ("air", 60.0)
    tl.record(120.0, "on", False)
    assert tl.clock(130.0) == ("on", 130.0)  # ведёт без потери с самого начала
    tl.record(140.0, "lost", False)
    assert tl.clock(150.0) == ("lost", 10.0)
    tl.record(160.0, "off", False)
    assert tl.clock(170.0) == ("off", None)


def test_day_totals():
    tl = StateTimeline()
    tl.record(0.0, "on", True)
    tl.record(30.0, "lost", True)
    tl.record(40.0, "on", False)
    tl.record(50.0, "lost", False)
    tl.record(60.0, "on", False)
    assert tl.air_total(70.0) == 40.0
    assert tl.losses == 2
