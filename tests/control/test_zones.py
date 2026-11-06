from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.control.zones import SCALE_RANGE, Zone, framing, zone_of


def test_zone_clamps_point_and_scale():
    z = Zone.clamped(1.5, -1.0, 9.0)
    assert (z.x, z.y) == (0.9, 0.1)
    assert z.scale == SCALE_RANGE[1]
    assert Zone.clamped(0.4, 0.3, 0.01).scale == SCALE_RANGE[0]


def test_soft_zone_never_wider_than_frame():
    e = EffortProfile.at(0.0)  # самая широкая мягкая зона
    _, scaled = framing("waist_center", 0.0, {"waist_center": [0.5, 0.25, SCALE_RANGE[1]]})
    assert scaled.soft_x <= 0.5 and scaled.soft_y <= 0.5
    assert scaled.soft_x > e.soft_x


def test_framing_without_zone_is_the_preset():
    comp, e = framing("full", 0.5, {})
    assert comp == COMPOSITIONS["full"]
    assert e == EffortProfile.at(0.5)


def test_framing_moves_point_and_scales_both_zones():
    comp, e = framing("waist_center", 0.5, {"waist_center": [0.38, 0.30, 1.5]})
    base = EffortProfile.at(0.5)
    assert comp.anchor == (0.38, 0.30)
    assert comp.key == "waist_center" and comp.u_frac == COMPOSITIONS["waist_center"].u_frac
    assert abs(e.dead_x - base.dead_x * 1.5) < 1e-9 and abs(e.soft_y - base.soft_y * 1.5) < 1e-9
    assert e.gain == base.gain  # характер движения не меняется


def test_zone_is_per_framing_size():
    zones = {"wide": [0.6, 0.4, 1.0]}
    assert framing("waist_center", 0.5, zones)[0] == COMPOSITIONS["waist_center"]
    assert framing("wide", 0.5, zones)[0].anchor == (0.6, 0.4)


def test_zone_of_ignores_garbage():
    assert zone_of({"wide": "x"}, "wide") is None
    assert zone_of({"wide": [0.5]}, "wide") is None
    assert zone_of({"wide": [0.5, 0.5, "a"]}, "wide") is None
    assert zone_of(None, "wide") is None
    assert zone_of({"wide": [2, 0.5, 1]}, "wide") == Zone(0.9, 0.5, 1.0)
