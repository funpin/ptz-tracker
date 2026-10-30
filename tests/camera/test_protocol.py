import pytest

from ptztracker.camera import protocol as PR
from ptztracker.camera.protocol import (
    CameraError,
    build_url,
    cgi_for,
    check_response,
    focus_auto,
    iris_abs,
    iris_auto,
    pan_tilt_abs,
    pan_tilt_speed,
    parse_model,
    parse_pan_tilt,
    parse_zoom,
    preset_recall,
    preset_store,
    zoom_abs,
    zoom_speed,
)


def test_pts():
    assert pan_tilt_speed(50, 50) == "#PTS5050"
    assert pan_tilt_speed(1, 99) == "#PTS0199"


@pytest.mark.parametrize("a", [0, 100, -1])
def test_pts_range(a):
    with pytest.raises(ValueError):
        pan_tilt_speed(a, 50)


def test_zoom():
    assert zoom_speed(75) == "#Z75"
    assert zoom_abs(0x555) == "#AXZ555"
    assert zoom_abs(0xFFF) == "#AXZFFF"


def test_zoom_abs_range():
    with pytest.raises(ValueError):
        zoom_abs(0x554)


def test_apc():
    assert pan_tilt_abs(0x8000, 0x8000) == "#APC80008000"


def test_focus_iris():
    assert focus_auto(True) == "#D11"
    assert focus_auto(False) == "#D10"
    assert iris_auto(True) == "#D31"
    assert iris_abs(0x555) == "#AXI555"


def test_presets():
    assert preset_recall(0) == "#R00"
    assert preset_store(9) == "#M09"
    with pytest.raises(ValueError):
        preset_recall(100)


def test_url():
    assert build_url("10.0.0.12", "#PTS5050") == "http://10.0.0.12/cgi-bin/aw_ptz?cmd=%23PTS5050&res=1"


def test_url_cam():
    assert build_url("10.0.0.12", "QID") == "http://10.0.0.12/cgi-bin/aw_cam?cmd=QID&res=1"


def test_url_with_port():
    assert build_url("127.0.0.1:5000", "#O") == "http://127.0.0.1:5000/cgi-bin/aw_ptz?cmd=%23O&res=1"


def test_url_escapes_colons_in_cam_command():
    assert build_url("h", "OSE:69:1") == "http://h/cgi-bin/aw_cam?cmd=OSE:69:1&res=1"


def test_cgi_for():
    assert cgi_for("#O") == "aw_ptz"
    assert cgi_for("QID") == "aw_cam"


def test_error():
    with pytest.raises(CameraError) as e:
        check_response("eR2:PTS")
    assert e.value.code == "er2"


def test_ok_passthrough():
    assert check_response("pTS5050") == "pTS5050"


def test_parse():
    assert parse_pan_tilt("aPC80008000") == (0x8000, 0x8000)
    assert parse_zoom("gz6A0") == 0x6A0
    assert parse_model("OID:AW-HE42") == "AW-HE42"


def test_parse_garbage():
    with pytest.raises(ValueError):
        parse_pan_tilt("p1")


# --- питание, gain, баланс белого (замер AW-HE40 2026-10-30, §7 спеки) ---


def test_parse_power():
    assert PR.parse_power("p1") == "on"
    assert PR.parse_power("p0") == "standby"
    assert PR.parse_power("p3") == "switching"
    for body in ("ER2:O", "", "x", "p9"):
        assert PR.parse_power(body) == "unknown"
    assert PR.POWER_ON == "#O1" and PR.POWER_STANDBY == "#O0"


def test_gain_codes():
    assert PR.GAIN_CODES[0] == 0x08 and PR.GAIN_CODES[-1] == 0x38 and len(PR.GAIN_CODES) == 17
    assert PR.gain(0x0B) == "OGU:0B"
    assert PR.gain(PR.GAIN_AUTO) == "OGU:80"
    for bad in (0x09, 0x39, 0x07):
        with pytest.raises(ValueError):
            PR.gain(bad)
    assert PR.gain_db(0x11) == 9
    assert PR.gain_db(0x80) is None
    assert PR.parse_gain("OGU:11") == 0x11
    with pytest.raises(ValueError):
        PR.parse_gain("ER1:QGU")


def test_wb():
    assert PR.wb_mode(9) == "OAW:9"
    for bad in (6, 7, 8, 10):
        with pytest.raises(ValueError):
            PR.wb_mode(bad)
    assert PR.parse_wb("OAW:9") == 9
    assert PR.WB_MODES[4] == "3200K" and PR.WB_MODES[9] == "VAR"


def test_color_temp():
    assert PR.color_temp(0x1A) == "OSD:B1:01A"
    assert PR.color_temp(PR.COLOR_TEMP_MAX) == "OSD:B1:04B"
    with pytest.raises(ValueError):
        PR.color_temp(0x4C)
    assert PR.parse_color_temp("OSD:B1:01A") == 0x1A
    assert PR.color_temp_kelvin(0x1A) == 5000


def test_build_url_cam():
    assert "/aw_cam?cmd=OGU:0B&res=1" in build_url("h", "OGU:0B")
