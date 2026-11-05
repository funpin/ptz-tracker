"""Намерения оператора, выданные геймпадом. Чистые данные: Qt и SDL здесь не нужны."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PanTilt:
    vx: float  # + вправо
    vy: float  # + вверх


@dataclass(frozen=True)
class Zoom:
    v: float  # + ближе


@dataclass(frozen=True)
class Preset:
    n: int  # 0–3
    store: bool  # True — длинное удержание, запомнить


@dataclass(frozen=True)
class IrisStep:
    d: int


@dataclass(frozen=True)
class GainStep:
    d: int


@dataclass(frozen=True)
class Neighbor:
    d: int  # −1 — левее


@dataclass(frozen=True)
class ToggleFollow:
    pass


@dataclass(frozen=True)
class Stop:
    pass


@dataclass(frozen=True)
class PushFocus:
    pass


@dataclass(frozen=True)
class Composition:
    d: int  # −1 — шире


@dataclass(frozen=True)
class SpeedStep:
    d: int  # +1 — быстрее (R1), −1 — медленнее (L1)


@dataclass(frozen=True)
class MenuOpen:
    pass


@dataclass(frozen=True)
class MenuNav:
    dx: int
    dy: int


@dataclass(frozen=True)
class MenuAccept:
    pass


@dataclass(frozen=True)
class MenuClose:
    pass


@dataclass(frozen=True)
class NextCamera:
    pass


@dataclass(frozen=True)
class SaveMoment:
    pass


@dataclass(frozen=True)
class Disconnected:
    pass


Intent = (PanTilt | Zoom | Preset | IrisStep | GainStep | Neighbor | ToggleFollow | Stop | PushFocus
          | Composition | MenuOpen | MenuNav | MenuAccept | MenuClose | NextCamera | SaveMoment | Disconnected)
