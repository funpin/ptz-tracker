"""Хранение настроек программы: список камер и их параметры.

Пароли в JSON не попадают — они лежат в системном хранилище (keyring).
"""

from __future__ import annotations

import dataclasses
import ipaddress
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import platformdirs

log = logging.getLogger(__name__)

APP_NAME = "PTZTracker"
STREAMS = ("auto", "h264_1", "h264_2", "mjpeg")
KEYRING_SERVICE = "PTZTracker"


@dataclass
class CameraConfig:
    id: str
    name: str
    ip: str
    kind: Literal["panasonic", "sim"] = "panasonic"
    username: str | None = None
    rtsp_url: str | None = None
    min_interval: float = 0.130
    stream: str = "auto"  # "auto" | "h264_1" | "h264_2" | "mjpeg"
    calibration: dict | None = None  # результат мастера калибровки (control.camera_model.Calibration.to_json)


@dataclass
class AppConfig:
    cameras: list[CameraConfig] = field(default_factory=list)
    last_camera_id: str | None = None
    sim_scene_path: str | None = None


def default_config() -> AppConfig:
    return AppConfig(cameras=[CameraConfig(id="cam-1", name="Камера 1", ip="10.0.0.12")])


def default_config_path() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False, roaming=True)) / "config.json"


_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def is_valid_ip(s: str) -> bool:
    """IPv4-адрес или имя хоста. Строка из одних чисел обязана быть корректным IPv4."""
    if not s or s != s.strip() or len(s) > 253:
        return False
    labels = s.split(".")
    if all(label.isdigit() for label in labels):
        try:
            ipaddress.IPv4Address(s)
        except ValueError:
            return False
        return True
    return all(_HOST_LABEL.match(label) for label in labels)


def _filter_fields(cls, data: dict) -> dict:
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def _camera_from(d) -> CameraConfig | None:
    """Камера из JSON; неправильные записи пропускаются с предупреждением."""
    try:
        c = CameraConfig(**_filter_fields(CameraConfig, d))
    except TypeError as e:
        log.warning("Пропускаю запись камеры %r: %s", d, e)
        return None
    ok = (
        isinstance(c.id, str)
        and isinstance(c.name, str)
        and isinstance(c.ip, str)
        and c.kind in ("panasonic", "sim")
        and (c.kind == "sim" or is_valid_ip(c.ip))
        and isinstance(c.min_interval, (int, float))
    )
    if not ok:
        log.warning("Пропускаю неправильную запись камеры: %r", d)
        return None
    if c.calibration is not None and not isinstance(c.calibration, dict):
        log.warning("Неправильная калибровка у камеры %s — сбрасываю", c.name)
        c.calibration = None
    if c.stream not in STREAMS:
        log.warning("Неизвестный поток %r у камеры %s — ставлю «Авто»", c.stream, c.name)
        c.stream = "auto"
    return c


class ConfigStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> AppConfig:
        if not self.path.exists():
            return default_config()
        try:
            raw = self.path.read_bytes()
        except OSError as e:
            log.error("Не удалось прочитать настройки (%s), беру настройки по умолчанию", e)
            return default_config()
        try:
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("ожидался JSON-объект")
            cameras = [c for c in (_camera_from(d) for d in data.get("cameras") or []) if c is not None]
            cfg = AppConfig(**_filter_fields(AppConfig, {**data, "cameras": cameras}))
        except (UnicodeDecodeError, ValueError, TypeError, AttributeError) as e:
            log.warning("Конфиг повреждён (%s), сохраняю копию и беру настройки по умолчанию", e)
            try:
                self.path.with_name(self.path.name + ".bak").write_bytes(raw)
            except OSError as e2:
                log.error("Не удалось сохранить копию повреждённого конфига: %s", e2)
            return default_config()
        if not cfg.cameras:
            cfg.cameras = default_config().cameras
        return cfg

    def save(self, cfg: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(dataclasses.asdict(cfg), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def get_password(camera_id: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, camera_id)
    except Exception as e:  # backend может отсутствовать
        log.warning("keyring недоступен: %s", e)
        return None


def set_password(camera_id: str, pw: str | None) -> None:
    try:
        import keyring

        if pw:
            keyring.set_password(KEYRING_SERVICE, camera_id, pw)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, camera_id)
            except keyring.errors.PasswordDeleteError:
                pass
    except Exception as e:
        log.warning("keyring недоступен: %s", e)
