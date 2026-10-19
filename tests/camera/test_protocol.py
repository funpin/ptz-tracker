import pytest

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
