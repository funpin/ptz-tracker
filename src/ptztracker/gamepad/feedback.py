"""Цвет световой полосы и вибро-паттерны геймпада (чистые функции, без Qt/SDL)."""
from __future__ import annotations

from dataclasses import dataclass

# Паттерн — кортеж сегментов (low, high, мс); (0, 0, мс) — пауза.
# Замер на железе: 0,5 силы × 200 мс не чувствуется, 1,0 × 1000 мс — чувствуется;
# поэтому всё на полной силе и импульс не короче 120 мс.
RUMBLE_PRESET_STORED = ((1.0, 1.0, 250),)
RUMBLE_NOWHERE = ((1.0, 1.0, 120),)  # короткий импульс; «слабость» передаёт только длина
RUMBLE_FOLLOW_ON = ((1.0, 1.0, 120), (0, 0, 120), (1.0, 1.0, 120))
RUMBLE_FOLLOW_AUTO_OFF = ((1.0, 1.0, 500),)
RUMBLE_LIMIT = ((1.0, 1.0, 120),)  # повтор раз в ~0,7 с — дело вызывающего

RED = (255, 0, 0)
ORANGE = (255, 110, 0)
VIOLET = (150, 0, 255)
CYAN = (0, 170, 255)
GREEN = (0, 200, 0)
DIM_WHITE = (40, 40, 40)
OFF = (0, 0, 0)


@dataclass(frozen=True)
class PadStatus:
    on_program: bool
    on_preview: bool
    link_ok: bool
    standby: bool
    menu_open: bool
    following: bool


def lightbar_color(s: PadStatus, t: float) -> tuple[int, int, int]:
    """Цвет полосы по приоритету; оранжевый мигает 1 Гц (чётные полсекунды — цвет)."""
    if s.on_program:
        return RED
    if not s.link_ok or s.standby:
        return ORANGE if int(t * 2) % 2 == 0 else OFF
    if s.menu_open:
        return VIOLET
    if s.following:
        return CYAN
    if s.on_preview:
        return GREEN
    return DIM_WHITE


_OPERATOR_REASONS = frozenset({
    "выключено оператором", "ручное управление", "аварийный стоп",
    "калибровка", "конец --lab", "разрешение агенту снято", "остановка камеры",
})


def is_auto_off(reason: str | None) -> bool:
    """Слежение выключилось само (не действием оператора)."""
    return reason not in _OPERATOR_REASONS
