from ptztracker.gamepad import feedback as fb
from ptztracker.gamepad.feedback import PadStatus, is_auto_off, lightbar_color


def st(**kw) -> PadStatus:
    base = dict(on_program=False, on_preview=False, link_ok=True,
                standby=False, menu_open=False, following=False)
    base.update(kw)
    return PadStatus(**base)


def test_priority_program_over_all():
    s = st(on_program=True, on_preview=True, link_ok=False, standby=True,
           menu_open=True, following=True)
    assert lightbar_color(s, 0.0) == (255, 0, 0)


def test_link_lost_blinks():
    s = st(link_ok=False, menu_open=True, following=True)
    assert lightbar_color(s, 0.2) == (255, 110, 0)
    assert lightbar_color(s, 0.7) == (0, 0, 0)
    assert lightbar_color(s, 1.2) == (255, 110, 0)
    assert lightbar_color(st(standby=True), 0.1) == (255, 110, 0)


def test_menu_over_following():
    assert lightbar_color(st(menu_open=True, following=True), 0) == (150, 0, 255)


def test_following_over_preview():
    s = st(following=True, on_preview=True)
    assert lightbar_color(s, 0) == (0, 170, 255)
    assert lightbar_color(st(on_preview=True), 0) == (0, 200, 0)


def test_idle_dim_white():
    assert lightbar_color(st(), 0) == (40, 40, 40)


def test_is_auto_off():
    for r in ("раскачка", "анализ остановился", "внутренняя ошибка", None):
        assert is_auto_off(r)
    for r in ("выключено оператором", "ручное управление", "аварийный стоп",
              "калибровка", "конец --lab", "разрешение агенту снято",
              "остановка камеры"):
        assert not is_auto_off(r)


def test_patterns_full_strength_min_pulse():
    for p in (fb.RUMBLE_PRESET_STORED, fb.RUMBLE_NOWHERE, fb.RUMBLE_FOLLOW_ON,
              fb.RUMBLE_FOLLOW_AUTO_OFF, fb.RUMBLE_LIMIT):
        for lo, hi, ms in p:
            assert (lo, hi) in ((1.0, 1.0), (0, 0))
            if (lo, hi) != (0, 0):
                assert ms >= 120
    assert fb.RUMBLE_FOLLOW_ON == ((1.0, 1.0, 120), (0, 0, 120), (1.0, 1.0, 120))
