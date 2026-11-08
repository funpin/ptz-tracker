"""Диск папки записи: сколько места именно на этом томе и подключён ли он. Без исключений."""

from __future__ import annotations

import logging
import shutil
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

RATE_CAMERA_BPS = 11.5e9 / 3600  # 25 Мбит/с: пока запись не идёт и фактической скорости нет
RATE_LIGHT_BPS = 1.4e9 / 3600  # «Лёгкий файл», 3000 кбит/с
_FAT32 = {"fat32", "vfat", "msdos"}


@dataclass(frozen=True)
class DiskInfo:
    connected: bool
    free: int
    total: int
    label: str  # буква (E:) или папка тома
    name: str  # имя тома; "" — неизвестно
    removable: bool | None
    fs: str


def is_fat32(info: DiskInfo) -> bool:
    return info.fs.lower() in _FAT32


def _volume_root(path: Path) -> Path:
    if sys.platform == "win32":
        return Path(path.anchor)
    parts = path.parts
    if sys.platform == "darwin" and len(parts) >= 3 and parts[1] == "Volumes":
        return Path(*parts[:3])
    return Path(path.anchor or "/")


def _nearest_existing(path: Path) -> Path:
    for p in (path, *path.parents):
        if p.exists():
            return p
    return path


def _filesystem(path: Path) -> str:
    best, fs = -1, ""
    text = str(path)
    for part in psutil.disk_partitions(all=False):
        mount = part.mountpoint
        if text.startswith(mount) and len(mount) > best:
            best, fs = len(mount), part.fstype
    return fs


def _windows_volume(root: Path) -> tuple[str, bool | None]:
    import ctypes

    name = ctypes.create_unicode_buffer(261)
    k32 = ctypes.windll.kernel32
    ok = k32.GetVolumeInformationW(str(root), name, 261, None, None, None, None, 0)
    return (name.value if ok else ""), k32.GetDriveTypeW(str(root)) == 2  # DRIVE_REMOVABLE


def _volume_details(root: Path) -> tuple[str, str, bool | None]:
    """(буква или папка, имя тома, съёмный)."""
    if sys.platform == "win32":
        name, removable = _windows_volume(root)
        return str(root).rstrip("\\"), name, removable
    if sys.platform == "darwin" and root.parent == Path("/Volumes"):
        return root.name, root.name, True
    return str(root), "", False if sys.platform == "darwin" else None


def _disconnected(path: Path) -> DiskInfo:
    return DiskInfo(False, 0, 0, _volume_root(path).name or str(_volume_root(path)), "", None, "")


def disk_info(folder: Path) -> DiskInfo:
    folder = Path(folder)
    try:
        root = _volume_root(folder)
        if not root.exists():  # иначе запас считался бы по родительскому тому (/Volumes лежит на системном диске)
            return _disconnected(folder)
        usage = shutil.disk_usage(_nearest_existing(folder))
    except OSError:
        return _disconnected(folder)
    try:
        label, name, removable = _volume_details(root)
        fs = _filesystem(root)
    except Exception:
        log.debug("Сведения о томе недоступны", exc_info=True)
        label, name, removable, fs = str(root), "", None, ""
    return DiskInfo(True, usage.free, usage.total, label, name, removable, fs)


class DiskWatch:
    """Фоновый опрос диска: окно не ждёт медленный или вынутый диск."""

    def __init__(self, folder: Callable[[], Path], interval_s: float = 5.0):
        self._folder = folder
        self.interval_s = interval_s
        self._lock = threading.Lock()
        self._info: DiskInfo | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._stop.clear()
        threading.Thread(target=self._run, name="disk-watch", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> DiskInfo | None:
        with self._lock:
            return self._info

    def _run(self) -> None:
        while not self._stop.is_set():
            info = disk_info(self._folder())
            with self._lock:
                self._info = info
            self._stop.wait(self.interval_s)
