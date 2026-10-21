from ptztracker.ui.state import TrackingView, tally_chip, tracking_view
from ptztracker.ui.theme import C


def test_view_off_without_reason():
    v = tracking_view(False, None, None, None)
    assert v == TrackingView(
        "off", "ВЫКЛ", "Кликните по человеку на видео, затем нажмите Пробел.",
        "ВЫКЛ", C.TEXT_FAINT, False)


def test_view_off_with_reason():
    v = tracking_view(False, "ручное управление", 3, None)
    assert v.kind == "off" and v.why == "Выключено: ручное управление"


def test_view_on():
    v = tracking_view(True, None, 7, "tracking")
    assert (v.kind, v.word, v.tag, v.color) == ("on", "ВЕДУ ЦЕЛЬ", "СЛЕЖЕНИЕ", C.ON)
    assert v.why == "Камера ведёт цель №7. Ручное действие или Esc выключат слежение."
    assert "№" not in tracking_view(True, None, None, None).why


def test_view_lost():
    v = tracking_view(True, None, 7, "lost")
    assert (v.kind, v.word, v.tag, v.color) == (
        "lost", "ЦЕЛЬ ПОТЕРЯНА", "ЦЕЛЬ ПОТЕРЯНА", C.LOST)
    assert v.why == "Камера стоит и ждёт. Человек вернётся в кадр — слежение продолжится."


def test_view_swing_has_calibrate():
    v = tracking_view(False, None, None, None, diverged=True)
    assert (v.kind, v.word, v.tag, v.color) == ("swing", "РАСКАЧКА", "РАСКАЧКА", C.FAULT)
    assert v.why == "Слежение остановлено: камера быстрее, чем ожидалось."
    assert v.show_calibrate
    assert not tracking_view(True, None, 1, "tracking").show_calibrate


def test_view_remote_wins():
    v = tracking_view(True, "x", 1, "lost", diverged=True, remote=True)
    assert (v.kind, v.word, v.tag, v.color) == ("remote", "ПУЛЬТ", "ПУЛЬТ", C.REMOTE)
    assert v.why == "Камерой управляют с пульта, слежение на паузе. Пробел — продолжить."


def test_tally_chip_variants():
    assert tally_chip(False, True, 1, "pgm") is None
    assert tally_chip(True, True, 2, "pgm") == ("ATEM · вход 2 · ЭФИР", "pgm")
    assert tally_chip(True, True, 2, "pvw") == ("ATEM · вход 2 · превью", "pvw")
    assert tally_chip(True, True, 2, None) == ("ATEM · вход 2", "neutral")
    assert tally_chip(True, False, 2, "pgm") == ("ATEM · нет связи", "neutral")
    assert tally_chip(True, True, 0, None) == ("ATEM · вход не задан", "neutral")
