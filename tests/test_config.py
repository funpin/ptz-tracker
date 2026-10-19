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
