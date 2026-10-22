import time

import pytest

from ptztracker.config import AppConfig, ConfigStore
from ptztracker.updates import UpdateChecker, UpdateInfo, is_newer, parse_version


def test_parse_version():
    assert parse_version("v0.3.0") == (0, 3, 0)
    assert parse_version("0.10.1") == (0, 10, 1)
    for bad in ("", "abc", "v1.2.3-rc1", None, 5):
        assert parse_version(bad) is None


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("v0.3.0", "0.2.0", True),
        ("0.2.0", "0.2.0", False),
        ("v0.2.0", "0.2.0", False),
        ("0.1.9", "0.2.0", False),
        ("0.10.0", "0.9.0", True),
        ("1.0", "0.9.9", True),
        ("garbage", "0.2.0", False),
        ("0.3.0", "garbage", False),
        (None, "0.2.0", False),
    ],
)
def test_is_newer(latest, current, expected):
    assert is_newer(latest, current) is expected


def run(fetch):
    c = UpdateChecker("0.2.0", fetch=fetch)
    c.start()
    deadline = time.time() + 3
    while not c.done and time.time() < deadline:
        time.sleep(0.01)
    assert c.done
    return c


def test_checker_success():
    c = run(lambda url: {"tag_name": "v0.3.0", "html_url": "https://github.com/x/y/releases/tag/v0.3.0"})
    assert c.result == UpdateInfo("0.3.0", "https://github.com/x/y/releases/tag/v0.3.0")
    assert c.ok


def test_checker_same_version_is_none_but_ok():
    c = run(lambda url: {"tag_name": "v0.2.0", "html_url": "https://github.com/x"})
    assert c.result is None and c.ok


def test_checker_exception_is_silent():
    def boom(url):
        raise OSError("offline")

    c = run(boom)
    assert c.result is None and not c.ok


@pytest.mark.parametrize("payload", [None, [], "x", {}, {"tag_name": 5}, {"tag_name": "v9.0.0"}])
def test_checker_bad_payload(payload):
    assert run(lambda url: payload).result is None


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_checker_ignores_draft_and_prerelease(flag):
    c = run(lambda url: {"tag_name": "v9.0.0", "html_url": "https://github.com/x", flag: True})
    assert c.result is None


def test_config_update_fields_validated(tmp_path):
    store = ConfigStore(tmp_path / "c.json")
    assert AppConfig().check_updates is True and AppConfig().last_update_check == 0.0
    store.path.write_text('{"check_updates": "yes", "last_update_check": "x"}')
    cfg = store.load()
    assert cfg.check_updates is True and cfg.last_update_check == 0.0
    store.path.write_text('{"check_updates": false, "last_update_check": 123}')
    cfg = store.load()
    assert cfg.check_updates is False and cfg.last_update_check == 123.0
