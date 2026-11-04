import pytest

from ptztracker.multicam.pads import PadFeedback, PadMove, PadRouter


def dots(fb):
    return [(f.pad, f.value) for f in fb if f.kind == "dots"]


def test_connect_takes_first_free():
    r = PadRouter()
    r.set_slots([True, True, False, False])
    p1, _, fb1 = r.connect()
    p2, _, fb2 = r.connect()
    assert (p1, r.slot_of(1)) == (1, 0)
    assert (p2, r.slot_of(2)) == (2, 1)
    assert dots(fb1) == [(1, 1)] and dots(fb2) == [(2, 2)]


def test_create_prefers_free():
    r = PadRouter()
    r.set_slots([True, True, True, False])
    r.connect()
    r.connect()
    moves, fb = r.create(1)
    assert r.slot_of(1) == 2
    assert moves == [PadMove(1, 0, 2)]
    assert not any(f.kind.startswith("rumble") for f in fb)


def test_create_when_all_taken_goes_next_and_rumbles():
    r = PadRouter()
    r.set_slots([True] * 4)
    for _ in range(4):
        r.connect()
    moves, fb = r.create(1)
    assert moves == [PadMove(1, 0, 1)]
    assert PadFeedback(1, "rumble_taken") in fb
    assert dots(fb) == [(1, 2)]


def test_single_camera_nowhere():
    r = PadRouter()
    r.set_slots([True, False, False, False])
    r.connect()
    moves, fb = r.create(1)
    assert moves == [] and r.slot_of(1) == 0
    assert fb == [PadFeedback(1, "rumble_nowhere")]


def test_closing_slot_moves_pads():
    r = PadRouter()
    r.set_slots([True, True, False, False])
    r.connect()
    moves, fb = r.set_slots([False, True, False, False])
    assert moves == [PadMove(1, 0, 1)]
    assert dots(fb) == [(1, 2)]


def test_no_cameras_dots_off():
    r = PadRouter()
    r.set_slots([True, False, False, False])
    r.connect()
    moves, fb = r.set_slots([False] * 4)
    assert r.slot_of(1) is None
    assert dots(fb) == [(1, 0)]
    assert r.create(1) == ([], [])


def test_opening_slot_assigns_idle_pad():
    r = PadRouter()
    r.connect()
    assert r.slot_of(1) is None
    r.set_slots([False, True, False, False])
    assert r.slot_of(1) == 1


def test_disconnect_frees_number():
    r = PadRouter()
    r.set_slots([True, True, False, False])
    r.connect()
    r.connect()
    assert r.disconnect(2) == [PadMove(2, 1, None)]
    assert r.connect()[0] == 2


def test_fifth_pad_raises():
    r = PadRouter()
    for _ in range(4):
        r.connect()
    with pytest.raises(ValueError):
        r.connect()
