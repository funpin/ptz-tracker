import json
import os

import pytest

from ptztracker.config import (
    CameraConfig,
    ConfigStore,
    default_config,
    is_valid_ip,
)


def test_missing_file_gives_default(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json").load()
    assert [c.ip for c in cfg.cameras] == ["10.0.0.12"]
    assert cfg.cameras[0].name == "Камера 1"


def test_roundtrip_cyrillic_path(tmp_path):
    p = tmp_path / "Пользователь" / "config.json"
    store = ConfigStore(p)
    cfg = default_config()
    cfg.cameras.append(CameraConfig(id="b", name="Сцена-2", ip="10.0.0.13"))
    store.save(cfg)
    assert store.load() == cfg


def test_corrupt_file_backed_up_and_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json", encoding="utf-8")
    cfg = ConfigStore(p).load()
    assert cfg == default_config()
    assert (tmp_path / "config.json.bak").read_text(encoding="utf-8") == "{not json"


def test_save_is_atomic(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "config.json")
    store.save(default_config())
    before = (tmp_path / "config.json").read_text(encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(os, "replace", boom)
    cfg = default_config()
    cfg.cameras[0].name = "Другое"
    with pytest.raises(OSError):
        store.save(cfg)
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == before


def test_unknown_fields_ignored_missing_defaulted(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(
        '{"cameras": [{"id": "x", "name": "А", "ip": "10.0.0.20", "zzz": 1}], "future": true}',
        encoding="utf-8",
    )
    cfg = ConfigStore(p).load()
    assert cfg.cameras[0].ip == "10.0.0.20"
    assert cfg.cameras[0].min_interval == 0.130
    assert cfg.cameras[0].kind == "panasonic"


def test_password_not_in_json(tmp_path):
    cfg = default_config()
    ConfigStore(tmp_path / "c.json").save(cfg)
    assert "password" not in (tmp_path / "c.json").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "s,ok",
    [
        ("10.0.0.12", True),
        ("cam-1.local", True),
        ("10.0.0", False),
        ("999.1.1.1", False),
        ("", False),
        ("10.0.0.12 ", False),
    ],
)
def test_is_valid_ip(s, ok):
    assert is_valid_ip(s) is ok


def test_utf16_file_does_not_crash(tmp_path):
    # Final review I4: файл, сохранённый Блокнотом в UTF-16
    p = tmp_path / "config.json"
    p.write_bytes('{"cameras": []}'.encode("utf-16"))
    cfg = ConfigStore(p).load()
    assert cfg == default_config()
    assert (tmp_path / "config.json.bak").exists()


def test_utf8_bom_accepted(tmp_path):
    p = tmp_path / "config.json"
    p.write_bytes(b"\xef\xbb\xbf" + '{"cameras": [{"id": "a", "name": "Сцена", "ip": "10.0.0.30"}]}'.encode("utf-8"))
    assert [c.ip for c in ConfigStore(p).load().cameras] == ["10.0.0.30"]


def test_invalid_camera_entries_skipped(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(
        '{"cameras": [{"id": "a", "name": "А", "ip": 5}, {"id": "b", "name": "Б", "ip": "10.0.0.31"}]}',
        encoding="utf-8",
    )
    assert [c.ip for c in ConfigStore(p).load().cameras] == ["10.0.0.31"]


def test_stream_roundtrip_and_invalid(tmp_path):
    store = ConfigStore(tmp_path / "c.json")
    cfg = default_config()
    assert cfg.cameras[0].stream == "auto"
    cfg.cameras[0].stream = "mjpeg_2"
    store.save(cfg)
    assert store.load().cameras[0].stream == "mjpeg_2"
    (tmp_path / "c.json").write_text(
        '{"cameras": [{"id": "a", "name": "А", "ip": "10.0.0.12", "stream": "zzz"}]}', encoding="utf-8"
    )
    assert store.load().cameras[0].stream == "auto"


def test_old_stream_names_migrate(tmp_path):
    store = ConfigStore(tmp_path / "c.json")
    for old, new in (("mjpeg", "mjpeg_1"), ("h264_2", "h264_1")):
        (tmp_path / "c.json").write_text(
            '{"cameras": [{"id": "a", "name": "А", "ip": "10.0.0.12", "stream": "%s"}]}' % old, encoding="utf-8"
        )
        assert store.load().cameras[0].stream == new


def test_record_settings_defaults_roundtrip_and_fallback(tmp_path):
    from ptztracker.config import AppConfig, record_dir

    d = AppConfig()
    assert (d.record_enabled, d.record_buffer_s, d.record_to_disk, d.record_dir) == (True, 300, False, "")
    assert record_dir(d).name == "Записи PTZ Tracker"
    store = ConfigStore(tmp_path / "config.json")
    cfg = default_config()
    cfg.record_to_disk, cfg.record_buffer_s, cfg.record_dir = True, 120, str(tmp_path / "rec")
    store.save(cfg)
    assert store.load() == cfg
    assert record_dir(cfg) == tmp_path / "rec"
    (tmp_path / "config.json").write_text(
        '{"record_enabled": "да", "record_buffer_s": -5, "record_to_disk": 1, "record_dir": 7}', encoding="utf-8"
    )
    bad = store.load()
    assert (bad.record_enabled, bad.record_buffer_s, bad.record_to_disk, bad.record_dir) == (True, 60, False, "")


@pytest.mark.parametrize("raw, expected", [
    ("Infinity", 300), ("-Infinity", 300), ("NaN", 300), ('"300"', 300), ("null", 300),
    ("5000", 900), ("10", 60), ("450.7", 450), (str(10**400), 900), ("-" + str(10**400), 60), ("1e400", 300),
])
def test_record_buffer_bad_values_do_not_break_start(tmp_path, raw, expected):
    p = tmp_path / "config.json"
    p.write_text('{"record_buffer_s": %s}' % raw, encoding="utf-8")
    assert ConfigStore(p).load().record_buffer_s == expected


def test_api_settings_defaults_token_and_fallback(tmp_path):
    from ptztracker.config import AppConfig, ensure_api_token

    d = AppConfig()
    assert (d.api_enabled, d.api_port, d.api_token) == (True, 8765, "")
    assert ensure_api_token(d) and len(d.api_token) >= 32
    tok = d.api_token
    assert not ensure_api_token(d) and d.api_token == tok
    p = tmp_path / "config.json"
    ConfigStore(p).save(d)
    assert ConfigStore(p).load().api_token == tok
    p.write_text('{"api_enabled": "yes", "api_port": 99999, "api_token": 5}', encoding="utf-8")
    cfg = ConfigStore(p).load()
    assert (cfg.api_enabled, cfg.api_port, cfg.api_token) == (True, 8765, "")


def test_old_config_without_atem_fields(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"cameras": [{"id": "a", "name": "A", "ip": "10.0.0.12"}]}), encoding="utf-8")
    cfg = ConfigStore(p).load()
    assert cfg.atem_ip == ""
    assert cfg.cameras[0].atem_input == 0


def test_atem_input_of_rejects_garbage():
    from ptztracker.tally import atem_input_of

    def cam(v):
        return CameraConfig(id="a", name="A", ip="10.0.0.12", atem_input=v)

    for bad in ("3", -1, 99, 0, None, 3.0, True):
        assert atem_input_of(cam(bad)) == 0, bad
    assert atem_input_of(cam(3)) == 3
    assert atem_input_of(cam(40)) == 40


def test_speed_scale_roundtrip_and_invalid(tmp_path):
    store = ConfigStore(tmp_path / "c.json")
    cfg = default_config()
    assert cfg.cameras[0].speed_scale is None
    cfg.cameras[0].speed_scale = {"pan": 1.4, "tilt": 1.0, "zoom": 0.8}
    store.save(cfg)
    assert store.load().cameras[0].speed_scale == {"pan": 1.4, "tilt": 1.0, "zoom": 0.8}
    (tmp_path / "c.json").write_text(
        '{"cameras": [{"id": "a", "name": "А", "ip": "10.0.0.12", "speed_scale": [1, 2]}]}', encoding="utf-8"
    )
    assert store.load().cameras[0].speed_scale is None


def test_stream_kind_by_url():
    from ptztracker.camera.session import CameraSession

    class Src:
        def __init__(self, url):
            self.url = url

    s = CameraSession.__new__(CameraSession)
    s.cfg = CameraConfig(id="a", name="А", ip="10.0.0.12")
    for url, kind in (
        ("http://h/cgi-bin/mjpeg?resolution=640x360&framerate=25", "mjpeg"),
        ("http://h/cgi-bin/mjpeg?resolution=1280x720", "mjpeg_2"),
        ("rtsp://h/MediaInput/h264/stream_1", "h264"),
    ):
        s.frames = Src(url)
        assert s.stream_kind() == kind


@pytest.mark.parametrize(
    "saved, fixed",
    [
        (
            "http://h/cgi-bin/mjpeg?resolution=1280x720",
            "http://h/cgi-bin/mjpeg?resolution=1280x720&framerate=25",
        ),
        (
            "http://h/cgi-bin/mjpeg?resolution=640x360",
            "http://h/cgi-bin/mjpeg?resolution=640x360&framerate=25",
        ),
        ("http://h/cgi-bin/mjpeg", "http://h/cgi-bin/mjpeg?framerate=25"),
        (
            "http://h/cgi-bin/mjpeg?resolution=1280x720&framerate=10",
            "http://h/cgi-bin/mjpeg?resolution=1280x720&framerate=10",
        ),
        ("rtsp://h/MediaInput/h264/stream_1", "rtsp://h/MediaInput/h264/stream_1"),
    ],
)
def test_saved_mjpeg_url_gets_framerate(tmp_path, saved, fixed):
    p = tmp_path / "c.json"
    p.write_text(
        json.dumps({"cameras": [{"id": "a", "name": "А", "ip": "10.0.0.12", "rtsp_url": saved}]}),
        encoding="utf-8",
    )
    assert ConfigStore(p).load().cameras[0].rtsp_url == fixed


def test_pad_number_roundtrip_and_old_config(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"cameras": [{"id": "a", "name": "A", "ip": "10.0.0.12"}]}), encoding="utf-8")
    store = ConfigStore(p)
    cfg = store.load()
    assert cfg.cameras[0].pad_number is None
    cfg.cameras[0].pad_number = 3
    store.save(cfg)
    assert store.load().cameras[0].pad_number == 3


def _load(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return ConfigStore(p).load()


def test_grid_default(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json").load()
    assert cfg.grid == [None] * 4
    assert cfg.max_analyzed == 1


def test_grid_from_last_camera_id(tmp_path):
    cfg = _load(tmp_path, {"last_camera_id": "cam-1"})
    assert cfg.grid == ["cam-1", None, None, None]


def test_grid_bad_shape_fixed(tmp_path):
    assert _load(tmp_path, {"grid": ["a", 5]}).grid == ["a", None, None, None]
    assert _load(tmp_path, {"grid": "x"}).grid == [None] * 4
    assert _load(tmp_path, {"grid": list("abcdef")}).grid == list("abcd")


def test_max_analyzed_clamped(tmp_path):
    assert _load(tmp_path, {"max_analyzed": 0}).max_analyzed == 1
    assert _load(tmp_path, {"max_analyzed": 9}).max_analyzed == 4
    assert _load(tmp_path, {"max_analyzed": "x"}).max_analyzed == 1


def test_camera_tracking_default_true(tmp_path):
    assert CameraConfig(id="a", name="A", ip="10.0.0.1").tracking is True
    cfg = _load(tmp_path, {"cameras": [{"id": "a", "name": "A", "ip": "10.0.0.1"}]})
    assert cfg.cameras[0].tracking is True


def test_grid_duplicates_and_empty_ids_dropped(tmp_path):
    assert _load(tmp_path, {"grid": ["a", "", "a", "b"]}).grid == ["a", None, None, "b"]
