"""Хранение настроек программы: список камер и их параметры.

Пароли в JSON не попадают — они лежат в системном хранилище (keyring).
"""

from __future__ import annotations

import dataclasses
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import platformdirs

from ptztracker.control.manual_curve import clamp_sens
from ptztracker.video.rtsp import with_mjpeg_framerate

log = logging.getLogger(__name__)

APP_NAME = "PTZTracker"
STREAMS = ("auto", "mjpeg_1", "mjpeg_2", "h264_1")
_OLD_STREAMS = {"mjpeg": "mjpeg_1", "h264_2": "h264_1"}  # прежние названия потоков в config.json
COMPOSITION_KEYS = ("waist_center", "waist_left", "waist_right", "full", "wide")
KEYRING_SERVICE = "PTZTracker"
GRID_SLOTS = 4


@dataclass
class CameraConfig:
    id: str
    name: str
    ip: str
    kind: Literal["panasonic", "sim"] = "panasonic"
    username: str | None = None
    rtsp_url: str | None = None
    min_interval: float = 0.130
    stream: str = "auto"  # "auto" | "mjpeg_1" | "mjpeg_2" | "h264_1"
    calibration: dict | None = None  # результат мастера калибровки (control.camera_model.Calibration.to_json)
    # поправка скорости по ходу слежения (control.speed_adapt): {"pan", "tilt", "zoom"} — камера быстрее
    # таблицы калибровки во столько раз; новая калибровка её сбрасывает. Без калибровки — с ярлыком таблицы
    # "table" (camera_model.DEFAULT_TABLE_ID): поправка без ярлыка найдена для прежней таблицы и не берётся
    speed_scale: dict | None = None
    composition: str = "waist_center"  # control.composition.COMPOSITIONS
    effort: float = 0.5  # 0 — мягко, 1 — резко
    atem_input: int = 0  # вход ATEM (1…), 0 — не задан
    pad_number: int | None = None  # устарело: точки на геймпаде = номер клетки; читается из старых конфигов
    tracking: bool = True  # камеру можно вести слежением (иначе только ручное управление)
    model: str | None = None  # ответ камеры на QID («AW-HE40»); ставит сессия при подключении, окно сохраняет
    firmware: str | None = None  # версия прошивки, если камера её сообщает; None — не известна
    # возможности, на которые камера ответила ER1 (camera.models.IMAGE_FEATURES): их кнопки скрываются или гаснут
    unsupported: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    cameras: list[CameraConfig] = field(default_factory=list)
    last_camera_id: str | None = None
    grid: list = field(default_factory=lambda: [None] * GRID_SLOTS)  # id камер в клетках сетки 2×2
    max_analyzed: int = 1  # сколько камер одновременно анализируются нейросетью (1…4)
    sim_scene_path: str | None = None
    # чёрный ящик (recorder): последние record_buffer_s секунд в памяти, F9 сохраняет их в zip
    record_enabled: bool = True
    record_buffer_s: int = 300
    record_to_disk: bool = False  # постоянная запись на диск (~450 МБ/час)
    record_dir: str = ""  # пусто — «Записи PTZ Tracker» на рабочем столе
    # локальный API для агента (только 127.0.0.1); токен создаётся при первом запуске, агент читает его отсюда
    api_enabled: bool = True
    api_port: int = 8765
    api_token: str = ""
    atem_ip: str = ""  # адрес микшера ATEM, пусто — Tally выключен
    check_updates: bool = True  # раз в сутки спрашивать GitHub, не вышла ли новая версия
    last_update_check: float = 0.0  # когда проверяли в последний раз (секунды с 1970)
    latest_version: str = ""  # новая версия, найденная последней проверкой; пусто — новее нет
    # ручное управление (control.manual_curve): 0 — «Плавно», 100 — «Резко»; мышь и стрелки — одна настройка,
    # геймпады — своя у каждого по опознавателю (серийный номер или «GUID:имя»), имя — для окна «Геймпады…»
    stick_sensitivity: int = 50
    slow_on_tele: bool = True  # на теле тот же наклон поворачивает медленнее
    pad_sensitivity: dict = field(default_factory=dict)
    pad_names: dict = field(default_factory=dict)
    # L1/R1 у каждого геймпада: "limit" — ступени предела скорости (по умолчанию), "sens" — чувствительность ±10
    pad_l1r1_mode: dict = field(default_factory=dict)
    pad_speed_gear: dict = field(default_factory=dict)  # ступень 1…4 = 25/50/75/100 %; нет записи — 4


PAD_L1R1_MODES = ("limit", "sens")
SPEED_GEARS = 4  # ступени предела скорости геймпада: 25, 50, 75, 100 %

RECORD_DIR_NAME = "Записи PTZ Tracker"
RECORD_BUFFER_MIN_S, RECORD_BUFFER_MAX_S = 60, 900  # в окне настроек — от 1 до 15 минут


def record_dir(cfg: AppConfig) -> Path:
    """Папка для записей: из настроек или «Записи PTZ Tracker» на рабочем столе."""
    if cfg.record_dir:
        return Path(cfg.record_dir).expanduser()
    return Path(platformdirs.user_desktop_dir()) / RECORD_DIR_NAME


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
    if c.speed_scale is not None and not isinstance(c.speed_scale, dict):
        log.warning("Неправильная поправка скорости у камеры %s — сбрасываю", c.name)
        c.speed_scale = None  # значения внутри проверяет Calibration.set_speed_scale
    for name in ("model", "firmware"):
        if getattr(c, name) is not None and not isinstance(getattr(c, name), str):
            log.warning("Неправильное поле %s у камеры %s — сбрасываю", name, c.name)
            setattr(c, name, None)
    if not isinstance(c.unsupported, list):
        c.unsupported = []
    c.unsupported = list(dict.fromkeys(f for f in c.unsupported if isinstance(f, str)))
    if c.composition not in COMPOSITION_KEYS:
        c.composition = "waist_center"
    if not isinstance(c.effort, (int, float)):
        c.effort = 0.5
    if not isinstance(c.tracking, bool):
        c.tracking = True
    c.effort = min(1.0, max(0.0, float(c.effort)))
    if isinstance(c.rtsp_url, str):
        c.rtsp_url = with_mjpeg_framerate(c.rtsp_url)
    if c.stream in _OLD_STREAMS:
        log.info("Поток %r у камеры %s переименован в %r", c.stream, c.name, _OLD_STREAMS[c.stream])
        c.stream = _OLD_STREAMS[c.stream]
    if c.stream not in STREAMS:
        log.warning("Неизвестный поток %r у камеры %s — ставлю «Авто»", c.stream, c.name)
        c.stream = "auto"
    return c


def _fix_record_settings(cfg: AppConfig) -> None:
    """Неправильные значения настроек записи → значения по умолчанию."""
    d = AppConfig()
    if not isinstance(cfg.record_enabled, bool):
        cfg.record_enabled = d.record_enabled
    if not isinstance(cfg.record_to_disk, bool):
        cfg.record_to_disk = d.record_to_disk
    b = cfg.record_buffer_s
    # int проверяем отдельно: math.isfinite(10**400) бросает OverflowError, а большой int просто обрезается
    if isinstance(b, bool) or not isinstance(b, (int, float)) or (isinstance(b, float) and not math.isfinite(b)):
        b = d.record_buffer_s
    cfg.record_buffer_s = int(min(RECORD_BUFFER_MAX_S, max(RECORD_BUFFER_MIN_S, b)))
    if not isinstance(cfg.record_dir, str):
        cfg.record_dir = d.record_dir


def _fix_grid(cfg: AppConfig) -> None:
    """Сетка камер: ровно 4 клетки (id или None); пустая — первая клетка из last_camera_id."""
    g = cfg.grid if isinstance(cfg.grid, list) else []
    g = [x if isinstance(x, str) and x else None for x in g[:GRID_SLOTS]]
    g = [x if x is None or x not in g[:i] else None for i, x in enumerate(g)]  # одна камера — одна клетка
    g += [None] * (GRID_SLOTS - len(g))
    if not any(g) and isinstance(cfg.last_camera_id, str):
        g[0] = cfg.last_camera_id
    cfg.grid = g
    m = cfg.max_analyzed
    if isinstance(m, bool) or not isinstance(m, int):
        m = 1
    cfg.max_analyzed = min(GRID_SLOTS, max(1, m))


def _fix_api_settings(cfg: AppConfig) -> None:
    d = AppConfig()
    if not isinstance(cfg.api_enabled, bool):
        cfg.api_enabled = d.api_enabled
    p = cfg.api_port
    if isinstance(p, bool) or not isinstance(p, int) or not 0 < p < 65536:
        cfg.api_port = d.api_port
    if not isinstance(cfg.api_token, str):
        cfg.api_token = d.api_token


def _fix_update_settings(cfg: AppConfig) -> None:
    d = AppConfig()
    if not isinstance(cfg.check_updates, bool):
        cfg.check_updates = d.check_updates
    t = cfg.last_update_check
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0:
        cfg.last_update_check = d.last_update_check
    else:
        cfg.last_update_check = float(t)
    if not isinstance(cfg.latest_version, str):
        cfg.latest_version = d.latest_version


def _fix_control_settings(cfg: AppConfig) -> None:
    d = AppConfig()

    def sens(v) -> int | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        return clamp_sens(v)

    s = sens(cfg.stick_sensitivity)
    cfg.stick_sensitivity = d.stick_sensitivity if s is None else s
    if not isinstance(cfg.slow_on_tele, bool):
        cfg.slow_on_tele = d.slow_on_tele
    ps = cfg.pad_sensitivity if isinstance(cfg.pad_sensitivity, dict) else {}
    cfg.pad_sensitivity = {k: sens(v) for k, v in ps.items() if isinstance(k, str) and k and sens(v) is not None}
    names = cfg.pad_names if isinstance(cfg.pad_names, dict) else {}
    cfg.pad_names = {k: v for k, v in names.items() if isinstance(k, str) and k and isinstance(v, str)}
    modes = cfg.pad_l1r1_mode if isinstance(cfg.pad_l1r1_mode, dict) else {}
    cfg.pad_l1r1_mode = {k: v for k, v in modes.items() if isinstance(k, str) and k and v in PAD_L1R1_MODES}
    gears = cfg.pad_speed_gear if isinstance(cfg.pad_speed_gear, dict) else {}
    cfg.pad_speed_gear = {k: max(1, min(SPEED_GEARS, v)) for k, v in gears.items()
                          if isinstance(k, str) and k and isinstance(v, int) and not isinstance(v, bool)}


def ensure_api_token(cfg: AppConfig) -> bool:
    """Создать токен API, если его ещё нет. True — токен новый (конфиг нужно сохранить)."""
    if cfg.api_token:
        return False
    cfg.api_token = secrets.token_urlsafe(24)
    return True


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
        _fix_record_settings(cfg)
        _fix_api_settings(cfg)
        _fix_grid(cfg)
        _fix_update_settings(cfg)
        _fix_control_settings(cfg)
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


# macOS спрашивает доступ к связке ключей при каждом чтении: читаем один раз за запуск
_pw_cache: dict[str, str | None] = {}


def get_password(camera_id: str) -> str | None:
    if camera_id in _pw_cache:
        return _pw_cache[camera_id]
    try:
        import keyring

        pw = keyring.get_password(KEYRING_SERVICE, camera_id)
    except Exception as e:  # backend может отсутствовать, оператор мог нажать «Отказать» — тогда спросим снова
        log.warning("keyring недоступен: %s", e)
        return None
    _pw_cache[camera_id] = pw
    return pw


def set_password(camera_id: str, pw: str | None) -> None:
    _pw_cache[camera_id] = pw or None
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
