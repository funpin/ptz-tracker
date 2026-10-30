from ptztracker.gamepad.device import RawPad
from ptztracker.gamepad.intents import (
    Composition,
    Disconnected,
    GainStep,
    IrisStep,
    MenuAccept,
    MenuClose,
    MenuNav,
    MenuOpen,
    Neighbor,
    NextCamera,
    PanTilt,
    Preset,
    PushFocus,
    SaveMoment,
    Stop,
    ToggleFollow,
    Zoom,
)
from ptztracker.gamepad.mapping import Mapper


def pad(buttons=(), **axes):
    return RawPad(axes=axes, buttons=frozenset(buttons))


def mk(menu=False):
    m = Mapper(lambda: menu)
    m.update(pad(), -1.0)  # первое подключение без нажатых кнопок
    return m


def test_deadzone():
    m = mk()
    assert not [i for i in m.update(pad(leftx=0.1), 0.0) if isinstance(i, PanTilt)]
    assert m.update(pad(leftx=1.0), 0.1) == [PanTilt(1.0, 0.0)]
    assert m.update(pad(lefty=-1.0), 0.2) == [PanTilt(0.0, 1.0)]


def test_triggers_zoom():
    m = mk()
    (z,) = m.update(pad(r2=0.5), 0.0)
    assert isinstance(z, Zoom) and 0 < z.v <= 0.5
    assert m.update(pad(l2=1.0), 0.1) == [Zoom(-1.0)]
    assert m.update(pad(), 0.2) == [Zoom(0.0)]


def test_change_only():
    m = mk()
    p = pad(leftx=0.8, r2=0.7)
    assert m.update(p, 0.0)
    assert m.update(p, 0.1) == []


def test_preset_short_recall():
    m = mk()
    assert m.update(pad({"cross"}), 0.0) == []
    assert m.update(pad(), 0.3) == [Preset(0, False)]


def test_preset_long_store():
    m = mk()
    m.update(pad({"triangle"}), 0.0)
    assert m.update(pad({"triangle"}), 0.5) == []
    assert m.update(pad({"triangle"}), 1.0) == [Preset(3, True)]
    assert m.update(pad({"triangle"}), 1.5) == []
    assert m.update(pad(), 1.6) == []


def test_preset_cancel_holds():
    m = mk()
    m.update(pad({"square"}), 0.0)
    m.update(pad({"square"}), 0.5)
    m.cancel_holds()
    assert m.update(pad(), 0.6) == []


def test_iris_autorepeat():
    m = mk()
    assert m.update(pad({"dpad_right"}), 0.0) == [IrisStep(+1)]
    n = 0
    t = 0.0
    while t < 1.0:
        t += 0.05
        out = m.update(pad({"dpad_right"}), t)
        if t < 0.4:
            assert out == []
        elif t >= 0.4:
            n += len(out)
    assert 3 <= n <= 5
    m.update(pad(), 1.1)
    assert m.update(pad({"dpad_up"}), 1.2) == [GainStep(+1)]


def test_neighbor_needs_recenter():
    m = mk()
    assert m.update(pad(rightx=0.7), 0.0) == [Neighbor(+1)]
    assert m.update(pad(rightx=0.7), 0.1) == []
    assert m.update(pad(rightx=0.0), 0.2) == []
    assert m.update(pad(rightx=-0.7), 0.3) == [Neighbor(-1)]


def test_buttons():
    m = mk()
    for b, want in [("r3", ToggleFollow()), ("touchpad", Stop()), ("l3", PushFocus()),
                    ("l1", Composition(-1)), ("r1", Composition(+1)), ("options", MenuOpen())]:
        assert m.update(pad({b}), 0.0) == [want]
        assert m.update(pad({b}), 0.1) == []
        m.update(pad(), 0.2)


def test_menu_mode():
    m = mk(menu=True)
    for b, want in [("dpad_down", [MenuNav(0, 1)]), ("dpad_left", [MenuNav(-1, 0)]), ("cross", [MenuAccept()]),
                    ("circle", [MenuClose()]), ("triangle", [MenuClose()]), ("options", [MenuClose()]),
                    ("square", []), ("touchpad", [Stop()])]:
        assert m.update(pad({b}), 0.0) == want
        m.update(pad(), 0.1)
    assert m.update(pad(leftx=1.0), 0.2) == [PanTilt(1.0, 0.0)]


def test_stop_latches_until_recenter():
    m = mk()
    assert m.update(pad(leftx=1.0), 0.0) == [PanTilt(1.0, 0.0)]
    m.latch_stop()
    assert m.update(pad(leftx=0.9), 0.1) == []
    assert m.update(pad(leftx=0.0), 0.2) == []
    assert m.update(pad(leftx=0.8), 0.3) == [PanTilt(0.77, 0.0)]  # (0.8-0.12)/0.88 после мёртвой зоны


def test_disconnect():
    m = Mapper(lambda: False)
    assert m.update(None, 0.0) == []
    m.update(pad(leftx=1.0), 0.1)
    assert m.update(None, 0.2) == [Disconnected()]
    assert m.update(None, 0.3) == []


def test_menu_r3_and_neighbor():
    m = mk(menu=True)
    assert m.update(pad({"r3"}), 0.0) == [ToggleFollow()]
    assert m.update(pad(rightx=0.7), 0.1) == [Neighbor(+1)]


def test_preset_release_after_poll_gap_does_nothing():
    m = mk()
    m.update(pad({"cross"}), 0.0)
    m.update(pad({"cross"}), 0.5)
    assert m.update(pad(), 1.2) == []
    m.update(pad({"cross"}), 2.0)
    m.update(pad({"cross"}), 2.1)
    assert m.update(pad(), 3.5) == []


def test_deflected_stick_at_connect_latched():
    m = Mapper(lambda: False)
    assert m.update(pad(leftx=1.0, r2=1.0), 0.0) == []
    assert m.update(pad(leftx=0.5), 0.1) == []
    assert m.update(pad(), 0.2) == []
    assert m.update(pad(leftx=1.0), 0.3) == [PanTilt(1.0, 0.0)]


def test_autorepeat_after_stall_single_step():
    m = mk()
    m.update(pad({"dpad_right"}), 0.0)
    assert m.update(pad({"dpad_right"}), 3.0) == [IrisStep(+1)]
    assert m.update(pad({"dpad_right"}), 3.05) == []


def test_buttons_held_at_connect_ignored():
    m = Mapper(lambda: False)
    assert m.update(pad({"cross", "r3"}), 0.0) == []
    assert m.update(pad(), 0.1) == []
    m.update(None, 0.2)
    assert m.update(pad({"cross", "dpad_up"}), 0.3) == []
    assert m.update(pad(), 0.4) == []


def test_neighbor_deflected_at_connect_disarmed():
    m = Mapper(lambda: False)
    assert m.update(pad(rightx=0.9), 0.0) == []
    assert m.update(pad(rightx=0.9), 0.1) == []
    assert m.update(pad(), 0.2) == []
    assert m.update(pad(rightx=0.9), 0.3) == [Neighbor(+1)]


def test_create_short_next_camera():
    m = mk()
    m.update(pad({"create"}), 0.0)
    assert m.update(pad(), 0.3) == [NextCamera()]


def test_create_long_saves_moment_once_release_silent():
    m = mk()
    m.update(pad({"create"}), 0.0)
    m.update(pad({"create"}), 0.5)
    assert m.update(pad({"create"}), 1.0) == [SaveMoment()]
    assert m.update(pad({"create"}), 1.5) == []
    assert m.update(pad(), 1.6) == []


def test_create_release_after_poll_gap_does_nothing():
    m = mk()
    m.update(pad({"create"}), 0.0)
    m.update(pad({"create"}), 0.1)
    assert m.update(pad(), 0.9) == []


def test_create_ignored_in_menu():
    m = mk(menu=True)
    m.update(pad({"create"}), 0.0)
    assert m.update(pad({"create"}), 1.5) == []
    assert m.update(pad(), 1.6) == []


def test_menu_nav_release():
    m = mk(menu=True)
    assert m.update(pad({"dpad_right"}), 0.0) == [MenuNav(1, 0)]
    assert m.update(pad(), 0.1) == [MenuNav(0, 0)]
    assert m.update(pad({"dpad_down"}), 0.2) == [MenuNav(0, 1)]
    assert m.update(pad(), 0.3) == []
    assert mk().update(pad({"dpad_left"}), 0.0) != [] and mk().update(pad(), 0.1) == []
