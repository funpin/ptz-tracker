"""Окно «Запись видео…»: папка и запас места именно на её диске."""

import threading
import time
from pathlib import Path

from ptztracker.config import AppConfig, ConfigStore
from ptztracker.ui import video_dialog as V
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.video_dialog import VideoDialog
from ptztracker.videorec.disk import DiskInfo

GB = 1_000_000_000


def internal():
    return DiskInfo(True, 180 * GB, 512 * GB, "C:", "Системный", False, "NTFS")


def make(qtbot, monkeypatch, info=None, cfg=None):
    monkeypatch.setattr(V, "disk_info", lambda folder: info or internal())
    d = VideoDialog(cfg or AppConfig(), camera_name="Сцена")
    qtbot.addWidget(d)
    qtbot.waitUntil(lambda: bool(d.disk_text.text()), timeout=2000)
    return d


def test_shows_free_space_and_estimates(qtbot, monkeypatch):
    d = make(qtbot, monkeypatch)
    assert "Свободно 180 ГБ из 512 ГБ" in d.disk_text.text()
    assert "одна камера — 15 ч" in d.disk_text.text() and "четыре — 3 ч 54 мин" in d.disk_text.text()
    assert d.disk_title.text() == "Системный (C:)" and "внутренний · NTFS" in d.disk_kind.text()


def test_terabyte_disk(qtbot, monkeypatch):
    d = make(qtbot, monkeypatch, DiskInfo(True, 412 * GB, 1000 * GB, "E:", "T7", True, "exfat"))
    assert "из 1 ТБ" in d.disk_text.text() and "съёмный · exfat" in d.disk_kind.text()


def test_estimate_follows_mode(qtbot, monkeypatch):
    d = make(qtbot, monkeypatch)
    d.mode_light.click()
    assert "одна камера — 128 ч" in d.disk_text.text()


def test_fat32_warning(qtbot, monkeypatch):
    d = make(qtbot, monkeypatch, DiskInfo(True, 48 * GB, 64 * GB, "F:", "USB", True, "FAT32"))
    assert "FAT32" in d.disk_warning.text() and "20 мин" in d.disk_warning.text() and not d.disk_warning.isHidden()


def test_no_warning_on_normal_disk(qtbot, monkeypatch):
    d = make(qtbot, monkeypatch)
    assert d.disk_warning.isHidden()


def test_disconnected_disk_is_red(qtbot, monkeypatch):
    d = make(qtbot, monkeypatch, DiskInfo(False, 0, 0, "E:", "", None, ""))
    assert "не подключён" in d.disk_text.text() and d.disk_card.property("bad") is True


def test_choose_folder_does_not_wait_for_disk(qtbot, monkeypatch):
    d = make(qtbot, monkeypatch)
    release = threading.Event()

    def slow(folder):
        release.wait(5)
        return DiskInfo(True, 5 * GB, 10 * GB, "D:", "Медленный", True, "NTFS")

    monkeypatch.setattr(V, "disk_info", slow)
    t0 = time.monotonic()
    d.choose_folder(Path("/другая"))
    assert time.monotonic() - t0 < 0.05
    release.set()
    qtbot.waitUntil(lambda: "Медленный" in d.disk_title.text(), timeout=2000)


def test_stale_answer_is_ignored(qtbot, monkeypatch):
    d = make(qtbot, monkeypatch)
    first_go, second_done = threading.Event(), threading.Event()

    def by_folder(folder):
        if folder == Path("/первая"):
            first_go.wait(5)
            return DiskInfo(True, 1, 2, "A:", "Старая", True, "NTFS")
        second_done.set()
        return DiskInfo(True, 1, 2, "B:", "Новая", True, "NTFS")

    monkeypatch.setattr(V, "disk_info", by_folder)
    d.choose_folder(Path("/первая"))
    d.choose_folder(Path("/вторая"))
    qtbot.waitUntil(lambda: "Новая" in d.disk_title.text(), timeout=2000)
    first_go.set()
    qtbot.wait(100)
    assert "Новая" in d.disk_title.text()


def test_apply_saves_folder_and_mode(qtbot, monkeypatch, tmp_path):
    cfg = AppConfig()
    d = make(qtbot, monkeypatch, cfg=cfg)
    d.choose_folder(tmp_path / "x")
    d.mode_light.click()
    d.apply()
    assert cfg.video_dir == str(tmp_path / "x") and cfg.video_mode == "light"


def test_untouched_folder_stays_default(qtbot, monkeypatch):
    cfg = AppConfig()
    d = make(qtbot, monkeypatch, cfg=cfg)
    d.apply()
    assert cfg.video_dir == ""


def test_example_name_uses_camera(qtbot, monkeypatch):
    d = make(qtbot, monkeypatch)
    assert d.example.text().startswith("Сцена_") and d.example.text().endswith(".mp4")


def test_window_menu_opens_dialog(qtbot, tmp_path, monkeypatch):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    seen = []
    monkeypatch.setattr(VideoDialog, "exec", lambda self: seen.append(self) or 0)
    w.video_settings_action.trigger()
    assert len(seen) == 1
    w.close()
