"""Кто из камер получает анализ кадров при ограничении по нагрузке."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CellState:
    name: str
    tracking_allowed: bool
    tracking_on: bool


def allocate_analysis(
    cells: Sequence[CellState | None], selected: int | Sequence[int] | None, limit: int
) -> frozenset[int]:
    """Индексы клеток, которым положен анализ.

    Порядок: все со включённым слежением (даже сверх лимита — не отнимаем),
    затем выбранные оператором (одна клетка или список по важности), затем остальные по порядку клеток.
    """
    result = {i for i, c in enumerate(cells) if c is not None and c.tracking_on}

    def take(i: int) -> None:
        c = cells[i]
        if c is not None and c.tracking_allowed and len(result) < limit:
            result.add(i)

    for i in ([selected] if isinstance(selected, int) else list(selected or [])):
        if 0 <= i < len(cells):
            take(i)
    for i in range(len(cells)):
        take(i)
    return frozenset(result)


def tracking_refusal(cells: Sequence[CellState | None], slot: int, limit: int) -> str | None:
    """Текст отказа, если включение слежения в `slot` превысит лимит; иначе None."""
    me = cells[slot]
    if me is None:
        return None
    if not me.tracking_allowed:
        return "Слежение на этой камере выключено в настройках камеры"
    if me.tracking_on:
        return None
    names = [c.name for c in cells if c is not None and c.tracking_on]
    if len(names) >= limit:
        return (
            f"Уже следит {', '.join(names)} — выключите там "
            "или увеличьте число камер в настройках"
        )
    return None
