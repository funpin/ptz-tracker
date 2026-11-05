from ptztracker.ui.state import TrackingView, recalibration_hint, tally_chip, tracking_view
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


def test_tally_chip_input_out_of_range():
    # микшер прислал меньше флагов, чем номер входа: состояние неизвестно, а не «не в эфире»
    assert tally_chip(True, True, 9, None, known=False) == ("ATEM · вход 9 вне диапазона", "warn")
    assert tally_chip(True, True, 2, None, known=True) == ("ATEM · вход 2", "neutral")
    assert tally_chip(True, False, 9, None, known=False) == ("ATEM · нет связи", "neutral")


# --- значки верхней панели ---

from ptztracker.ui.state import analysis_pill, atem_pill, power_view, video_pill  # noqa: E402
from ptztracker.video.frames import SourceStats  # noqa: E402


def test_video_pill_ok():
    text, kind, tip = video_pill(SourceStats(True, 25.2, 1280, 720, None, "http://10.0.0.1/mjpeg"), "Авто")
    assert (text, kind) == ("1280×720 · 25", "ok")
    assert "Авто" in tip


def test_video_pill_error_text_only_in_tooltip():
    err = ("[Errno 1094995529] Invalid data found when processing input: "
           "'rtsp://admin:***@10.0.0.12:554/MediaInput/h264/stream_1'")
    text, kind, tip = video_pill(SourceStats(False, 0, 0, 0, err, "rtsp://admin:***@10.0.0.12:554/x"), "Авто")
    assert kind == "fault" and len(text) <= 14 and "Errno" not in text
    assert "Errno" in tip and "secret" not in tip


def test_video_pill_no_session():
    assert video_pill(None, "Авто")[1] == "off"


def test_analysis_pill_states():
    assert analysis_pill(None, 14.4, 31.0, "DirectML")[:2] == ("14 к/с", "ok")
    assert "31 мс" in analysis_pill(None, 14.4, 31.0, "DirectML")[2]
    text, kind, tip = analysis_pill(None, None, None, None)
    assert kind == "warn" and "загрузка" in tip.lower()
    assert analysis_pill("нет модели", None, None, None)[1] == "fault"


def test_atem_pill():
    assert atem_pill(False, False, 0, None) is None
    assert atem_pill(True, False, 3, None)[:2] == ("ATEM", "off")
    assert atem_pill(True, True, 0, None)[:2] == ("ATEM", "off")
    assert atem_pill(True, True, 3, "pgm")[:2] == ("ATEM 3 · PGM", "pgm")
    assert atem_pill(True, True, 3, "pvw")[:2] == ("ATEM 3 · PVW", "pvw")
    assert atem_pill(True, True, 3, None)[:2] == ("ATEM 3", "ok")
    assert atem_pill(True, True, 9, None, known=False)[1] == "warn"
    assert atem_pill(True, True, 3, "pgm")[2] == tally_chip(True, True, 3, "pgm")[0]


def test_power_view():
    assert power_view("standby", True) == "standby"
    assert power_view("switching", True) == "switching"
    assert power_view("on", False) == "offline"
    assert power_view("unknown", True) == "on"
    assert power_view("standby", False) == "offline"


def test_recalibration_hint_none_when_consistent_or_uncalibrated():
    calib = {"model": "AW-HE42", "firmware": "1.54"}
    assert recalibration_hint(calib, "AW-HE42", "1.54") is None
    assert recalibration_hint(calib, None, None) is None  # камера ещё не ответила на QID
    assert recalibration_hint(calib, "AW-HE42", None) is None  # прошивку камера не сообщила: сравнивать нечего
    assert recalibration_hint(calib, "aw-he42 ", "1.54") is None  # регистр и пробелы не в счёт
    assert recalibration_hint(None, "AW-HE40", None) is None  # без калибровки есть своё сообщение


def test_recalibration_hint_other_model_names_both():
    h = recalibration_hint({"model": "AW-HE42"}, "AW-HE40", None)
    assert h and "AW-HE42" in h and "AW-HE40" in h and "заново" in h


def test_recalibration_hint_other_firmware():
    h = recalibration_hint({"model": "AW-HE40", "firmware": "1.54"}, "AW-HE40", "1.60")
    assert h and "1.54" in h and "1.60" in h and "прошив" in h.lower()


def test_recalibration_hint_old_calibration_without_model():
    h = recalibration_hint({"pan": {}}, None, None)
    assert h and "заново" in h and "прежн" in h
    assert recalibration_hint({"model": None}, "AW-HE42", None) == h
