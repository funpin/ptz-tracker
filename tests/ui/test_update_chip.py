import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow
from ptztracker.updates import UpdateInfo


class FakeChecker:
    instances: list = []

    def __init__(self, current, info=None, ok=True):
        self.current, self._info, self.ok = current, info, ok
        self.started = 0
        self.done = False
        FakeChecker.instances.append(self)

    @property
    def result(self):
        return self._info if self.done else None

    def start(self):
        self.started += 1
        self.done = True


def factory(info=None, ok=True):
    FakeChecker.instances = []
    return lambda current: FakeChecker(current, info, ok)


def make(qtbot, tmp_path, cfg=None, **kw):
    store = ConfigStore(tmp_path / "c.json")
    if cfg is not None:
        store.save(cfg)
    w = MainWindow(store, use_sim=True, **kw)
    qtbot.addWidget(w)
    w.show()
    return w


def cfg_with(**kw):
    from ptztracker.config import default_config

    cfg = default_config()
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def test_chip_hidden_by_default(qtbot, tmp_path):
    w = make(qtbot, tmp_path, checker_factory=factory())
    assert w.update_chip.isHidden()
    assert w.update_chip.kind() == "accent"


def test_new_version_shows_chip_and_click_opens_url(qtbot, tmp_path, monkeypatch):
    info = UpdateInfo("0.3.0", "https://github.com/funpin/ptz-tracker/releases/tag/v0.3.0")
    w = make(qtbot, tmp_path, checker_factory=factory(info))
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda u: opened.append(u.toString()) or True)
    w._start_update_check()
    w._update_status()
    assert w.update_chip.isVisible()
    assert w.update_chip.text() == "Доступна 0.3.0"
    assert w.update_chip.toolTip() == "Открыть страницу релиза на GitHub"
    qtbot.mouseClick(w.update_chip, Qt.LeftButton)
    assert opened == [info.html_url]


def test_check_saves_time_when_ok(qtbot, tmp_path):
    w = make(qtbot, tmp_path, checker_factory=factory(None, ok=True))
    w._start_update_check()
    w._update_status()
    assert abs(w.cfg.last_update_check - time.time()) < 5
    assert ConfigStore(tmp_path / "c.json").load().last_update_check == w.cfg.last_update_check


def test_failed_check_does_not_save_time(qtbot, tmp_path):
    w = make(qtbot, tmp_path, checker_factory=factory(None, ok=False))
    w._start_update_check()
    w._update_status()
    assert w.cfg.last_update_check == 0.0
    assert w.update_chip.isHidden()


def test_disabled_setting_does_not_start_checker(qtbot, tmp_path):
    w = make(qtbot, tmp_path, cfg_with(check_updates=False), checker_factory=factory())
    w._start_update_check()
    assert FakeChecker.instances == []


def test_throttle_24h(qtbot, tmp_path):
    w = make(qtbot, tmp_path, cfg_with(last_update_check=time.time() - 3600), checker_factory=factory())
    w._start_update_check()
    assert FakeChecker.instances == []
    w.cfg.last_update_check = time.time() - 25 * 3600
    w._start_update_check()
    assert len(FakeChecker.instances) == 1 and FakeChecker.instances[0].started == 1


def test_menu_toggle_saves(qtbot, tmp_path):
    w = make(qtbot, tmp_path, checker_factory=factory())
    assert w.check_updates_action.isCheckable() and w.check_updates_action.isChecked()
    w.check_updates_action.trigger()
    assert w.cfg.check_updates is False
    assert ConfigStore(tmp_path / "c.json").load().check_updates is False


def test_known_newer_version_shown_after_restart_within_24h(qtbot, tmp_path):
    # проверка была час назад и нашла новую версию: после перезапуска значок виден сразу, без сети
    w = make(qtbot, tmp_path, cfg_with(last_update_check=time.time() - 3600, latest_version="99.0.0"),
             checker_factory=factory())
    w._start_update_check()
    assert FakeChecker.instances == []
    assert w.update_chip.isVisible() and "99.0.0" in w.update_chip.text()


def test_latest_version_remembered_and_cleared(qtbot, tmp_path):
    w = make(qtbot, tmp_path, checker_factory=factory(UpdateInfo("99.0.0", "https://example.org/r")))
    w._start_update_check()
    w._poll_update_check()
    assert w.cfg.latest_version == "99.0.0"
    w2 = make(qtbot, tmp_path / "b", checker_factory=factory(None))
    w2.cfg.latest_version = "99.0.0"
    w2._start_update_check()
    w2._poll_update_check()
    assert w2.cfg.latest_version == ""
