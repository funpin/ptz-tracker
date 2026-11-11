from ptztracker.gamepad.device import RawPad
from ptztracker.gamepad.intents import (
    Composition,
    Disconnected,
    GainStep,
    Help,
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
    SpeedStep,
    Stop,
    ToggleFollow,
    ToggleVideo,
    Zoom,
)
from ptztracker.gamepad.mapping import Mapper


def pad(buttons=(), touch=None, **axes):
    return RawPad(axes=axes, buttons=frozenset(buttons), touch=touch)


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
                    ("l1", SpeedStep(-1)), ("r1", SpeedStep(+1)), ("options", MenuOpen()), ("ps", Help())]:
        assert m.update(pad({b}), 0.0) == [want]
        assert m.update(pad({b}), 0.1) == []
        m.update(pad(), 0.2)


def test_menu_mode():
    m = mk(menu=True)
    for b, want in [("dpad_down", [MenuNav(0, 1)]), ("dpad_left", [MenuNav(-1, 0)]), ("cross", [MenuAccept()]),
                    ("circle", [MenuClose()]), ("triangle", [MenuClose()]), ("options", [MenuClose()]),
                    ("square", []), ("touchpad", [Stop()]), ("ps", [Help()])]:
        assert m.update(pad({b}), 0.0) == want
        m.update(pad(), 0.1)
    assert m.update(pad(leftx=1.0), 0.2) == [PanTilt(1.0, 0.0)]


def test_stop_latches_until_recenter():
    m = mk()
    assert m.update(pad(leftx=1.0), 0.0) == [PanTilt(1.0, 0.0)]
    m.latch_stop()
    assert m.update(pad(leftx=0.9), 0.1) == []
    assert m.update(pad(leftx=0.0), 0.2) == []
    m.sensitivity = 100  # линейная кривая: видно саму мёртвую зону
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


def test_create_long_toggles_video_once_release_silent():
    m = mk()
    m.update(pad({"create"}), 0.0)
    m.update(pad({"create"}), 0.5)
    assert m.update(pad({"create"}), 1.0) == [ToggleVideo()]  # было «сохранить момент» — решение оператора 2026-11-10
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


def test_stick_curve_after_deadzone():
    half = 0.12 + 0.88 * 0.5  # половина хода после мёртвой зоны
    for sens, want in ((100, 0.5), (50, 0.25), (0, 0.13)):
        m = mk()
        m.sensitivity = m.tilt_sensitivity = sens
        assert m.update(pad(leftx=half, lefty=-half), 0.0) == [PanTilt(want, want)]
        assert m.update(pad(leftx=-1.0), 0.1) == [PanTilt(-1.0, 0.0)]  # полный наклон — полная скорость


def test_default_sensitivity_softer_than_101():
    m = mk()
    assert m.sensitivity == 50
    assert m.update(pad(leftx=0.12 + 0.88 * 0.5), 0.0) == [PanTilt(0.25, 0.0)]


def test_curve_does_not_touch_zoom():
    m = mk()
    m.sensitivity = 0
    assert m.update(pad(r2=0.12 + 0.88 * 0.5), 0.0) == [Zoom(0.5)]


def swipe(m, x0, x1, t0=0.0, dur=0.2, y0=0.5, y1=0.5, buttons=(), steps=4):
    """Палец от (x0, y0) до (x1, y1) за dur секунд, затем отпускание; намерения за всё касание."""
    out = []
    for i in range(steps + 1):
        f = i / steps
        out += m.update(pad(buttons, touch=(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)), t0 + dur * f)
    out += m.update(pad(), t0 + dur + 0.01)
    return out


def test_swipe_right_wider_left_closer():
    m = mk()
    assert swipe(m, 0.2, 0.8) == [Composition(-1)]
    assert swipe(m, 0.8, 0.2, t0=1.0) == [Composition(+1)]


def test_short_slow_vertical_or_pressed_touch_is_not_swipe():
    m = mk()
    assert swipe(m, 0.4, 0.6) == []                          # меньше трети ширины
    assert swipe(m, 0.2, 0.8, t0=1.0, dur=1.0) == []         # дольше 0,8 с
    assert swipe(m, 0.3, 0.7, t0=3.0, y0=0.0, y1=1.0) == []  # по вертикали больше
    out = swipe(m, 0.2, 0.8, t0=5.0, buttons={"touchpad"})   # нажатие тачпада — это стоп, не свайп
    assert out == [Stop()]


def test_swipe_ignored_in_menu_and_after_reconnect_mid_touch():
    assert swipe(mk(menu=True), 0.2, 0.8) == []
    m = Mapper(lambda: False)
    m.update(pad(touch=(0.2, 0.5)), 0.0)  # палец уже на тачпаде при подключении
    m.update(pad(touch=(0.8, 0.5)), 0.1)
    assert m.update(pad(), 0.15) == []


def test_swipe_lost_on_poll_gap():
    m = mk()
    m.update(pad(touch=(0.2, 0.5)), 0.0)
    assert m.update(pad(touch=(0.8, 0.5)), 0.5) == []  # пропуск опросов дольше STALL_S
    assert m.update(pad(), 0.51) == []


def test_swipe_rejected_when_lift_comes_after_poll_gap():
    m = mk()
    m.update(pad(touch=(0.2, 0.5)), 0.0)
    m.update(pad(touch=(0.6, 0.5)), 0.1)
    assert m.update(pad(), 0.6) == []  # опрос стоял 0,5 с: где был палец при отпускании — неизвестно


def test_tilt_has_its_own_curve():
    """Пользователь, 2026-11-06: вверх/вниз ощущается грубее — у наклона своя чувствительность."""
    m = mk()
    m.sensitivity, m.tilt_sensitivity = 100, 0
    out = m.update(pad(leftx=0.12 + 0.88 * 0.5, lefty=-(0.12 + 0.88 * 0.5)), 0.0)
    assert out == [PanTilt(0.5, 0.13)]  # 0,5³
