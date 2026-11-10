import json

from ptztracker.config import AppConfig, CameraConfig, ConfigStore, default_config


def test_defaults_and_old_config(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"cameras": [{"id": "a", "name": "A", "ip": "10.0.0.12"}]}), encoding="utf-8")
    cfg = ConfigStore(p).load()
    assert (cfg.videohub_ip, cfg.videohub_output, cfg.videohub_follow) == ("", 0, True)
    assert cfg.cameras[0].videohub_input == 0


def test_roundtrip(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    cfg = default_config()
    cfg.videohub_ip, cfg.videohub_output, cfg.videohub_follow = "10.10.10.50", 5, False
    cfg.cameras[0].videohub_input = 3
    store.save(cfg)
    got = store.load()
    assert (got.videohub_ip, got.videohub_output, got.videohub_follow) == ("10.10.10.50", 5, False)
    assert got.cameras[0].videohub_input == 3


def test_garbage_fixed(tmp_path):
    p = tmp_path / "config.json"
    for ip, out, follow in ((5, "abc", "yes"), (None, -1, 1), ("  10.10.10.50 ", True, None), ("x", 999, 0)):
        p.write_text(json.dumps({"cameras": [{"id": "a", "name": "A", "ip": "10.0.0.12"}],
                                 "videohub_ip": ip, "videohub_output": out, "videohub_follow": follow}),
                     encoding="utf-8")
        cfg = ConfigStore(p).load()
        assert isinstance(cfg.videohub_ip, str)
        assert cfg.videohub_output == 0
        assert cfg.videohub_follow is True
    assert cfg.videohub_ip == "x"  # адрес проверяет окно; здесь — только тип


def test_fields_exist():
    assert AppConfig().videohub_ip == ""
    assert CameraConfig(id="a", name="A", ip="1.2.3.4").videohub_input == 0
