"""Профили моделей камер: только то, что замерено на живых AW-HE42 и AW-HE40 (§7 спеки).

Числа лежат в `he42.py` (единственный источник замеров); профиль на них ссылается и добавляет отличия модели.
Неизвестная модель берёт профиль HE42: на нём построены таблица скоростей и симулятор.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ptztracker.camera import he42

log = logging.getLogger(__name__)

# Возможности, которые камера может не знать (отвечает ER1); команды к ним — camera.session._feature_of
IMAGE_FEATURES = ("gain", "wb", "color_temp", "power")
DEFAULT_MIN_INTERVAL_S = 0.130  # так в спецификации Panasonic; тот же по умолчанию в CameraConfig


@dataclass(frozen=True)
class ModelProfile:
    name: str
    # gain / баланс белого / питание пробованы на живой камере этой модели (HE40 — да, HE42 — пока нет)
    image_controls_verified: bool
    # поворот на теле замедляется (замер HE40; у HE42 провал быстрых кодов на FFF — похоже, то же самое)
    tele_pan_deg_s: dict[int, dict[int, float]]
    tele_start_gz: int
    min_interval: float
    pan_deg_s_codes: tuple[int, ...]  # коды единственной таблицы скоростей he42.PAN_DEG_S
    speed_note: str = ""  # справка: чем скорости модели отличаются от таблицы (на работу не влияет)


_HE42 = ModelProfile(
    name="AW-HE42",
    image_controls_verified=False,
    tele_pan_deg_s=he42.TELE_PAN_DEG_S,
    tele_start_gz=he42.TELE_START_GZ,
    min_interval=DEFAULT_MIN_INTERVAL_S,
    pan_deg_s_codes=tuple(sorted(he42.PAN_DEG_S)),
)
_HE40 = ModelProfile(
    name="AW-HE40",
    image_controls_verified=True,
    tele_pan_deg_s=he42.TELE_PAN_DEG_S,
    tele_start_gz=he42.TELE_START_GZ,
    min_interval=DEFAULT_MIN_INTERVAL_S,
    pan_deg_s_codes=tuple(sorted(he42.PAN_DEG_S)),
    speed_note=(f"на кодах {he42.HE40_FASTER_CODES[0]}–{he42.HE40_FASTER_CODES[1]} быстрее таблицы на "
                f"{he42.HE40_FASTER_PCT[0]}–{he42.HE40_FASTER_PCT[1]}%"),
)
PROFILES: dict[str, ModelProfile] = {p.name: p for p in (_HE42, _HE40)}
DEFAULT_PROFILE = _HE42

_warned: set[str | None] = set()


def profile_for(model: str | None) -> ModelProfile:
    """Профиль по ответу `QID` (например «AW-HE40»; суффиксы вроде «AW-HE40SKEJ» допустимы).
    Неизвестная или ещё не определённая модель — профиль HE42, в журнал — предупреждение (по разу на значение)."""
    key = (model or "").strip().upper()
    for name, prof in PROFILES.items():
        if key.startswith(name):
            return prof
    if model is not None and model not in _warned:  # None — «ещё не спрашивали», не «неизвестна»
        _warned.add(model)
        log.warning("Модель камеры %r неизвестна — беру профиль %s", model, DEFAULT_PROFILE.name)
    return DEFAULT_PROFILE
