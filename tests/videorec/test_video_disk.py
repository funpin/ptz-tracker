"""Диск папки записи: запас места именно этого тома, вынутый диск."""

import shutil
import sys
from collections import namedtuple
from pathlib import Path

import pytest

from ptztracker.videorec import disk as D
from ptztracker.videorec.disk import DiskInfo, DiskWatch, disk_info, is_fat32


def info(fs):
    return DiskInfo(True, 1, 2, "E:", "", True, fs)


def test_existing_folder_matches_disk_usage(tmp_path):
    got = disk_info(tmp_path)
    u = shutil.disk_usage(tmp_path)
    assert got.connected and got.total == u.total and abs(got.free - u.free) < 500_000_000


def test_missing_folder_uses_existing_parent(tmp_path):
    got = disk_info(tmp_path / "ещё" / "нет")
    assert got.connected and got.total == shutil.disk_usage(tmp_path).total


@pytest.mark.skipif(sys.platform != "darwin", reason="тома Mac лежат в /Volumes")
def test_folder_on_removed_mac_volume_is_not_connected():
    got = disk_info(Path("/Volumes/НетТакогоДиска/Записи"))
    assert not got.connected and got.free == 0


def test_folder_on_removed_drive_is_not_connected(monkeypatch):
    monkeypatch.setattr(D, "_volume_root", lambda p: Path("/нет/такого/тома"))
    got = disk_info(Path("/нет/такого/тома/Записи"))
    assert not got.connected and got.free == 0


def test_disk_usage_failure_means_not_connected(tmp_path, monkeypatch):
    def boom(_):
        raise OSError("диск вынут")

    monkeypatch.setattr(D.shutil, "disk_usage", boom)
    assert not disk_info(tmp_path).connected


@pytest.mark.parametrize("fs,want", [("FAT32", True), ("vfat", True), ("msdos", True), ("exfat", False), ("NTFS", False)])
def test_is_fat32(fs, want):
    assert is_fat32(info(fs)) is want


def test_filesystem_comes_from_longest_mount(tmp_path, monkeypatch):
    Part = namedtuple("Part", "device mountpoint fstype opts")
    parts = [Part("a", "/", "apfs", ""), Part("b", str(tmp_path), "exfat", "")]
    monkeypatch.setattr(D.psutil, "disk_partitions", lambda all=False: parts)
    monkeypatch.setattr(D, "_volume_root", lambda p: tmp_path)
    assert disk_info(tmp_path).fs == "exfat"


def test_watch_follows_the_folder(tmp_path):
    w = DiskWatch(lambda: tmp_path, interval_s=0.01)
    assert w.snapshot() is None
    w.start()
    try:
        import time

        end = time.monotonic() + 3
        while w.snapshot() is None and time.monotonic() < end:
            time.sleep(0.01)
        assert w.snapshot().connected
    finally:
        w.stop()
