"""Маршрутизатор геймпадов: какой геймпад управляет какой клеткой сетки."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

MAX_PADS = 4


@dataclass(frozen=True)
class PadFeedback:
    pad: int
    kind: Literal["dots", "rumble_taken", "rumble_nowhere"]
    value: int = 0  # для dots: номер клетки 1–4 или 0


@dataclass(frozen=True)
class PadMove:
    pad: int
    old_slot: int | None
    new_slot: int | None


class PadRouter:
    """Геймпады 1–4 и клетки 0–3. Только логика, без Qt и ввода."""

    def __init__(self) -> None:
        self._open: list[bool] = [False] * 4
        self._slot: dict[int, int | None] = {}

    def slot_of(self, pad: int) -> int | None:
        return self._slot.get(pad)

    def pads_on(self, slot: int) -> list[int]:
        return sorted(p for p, s in self._slot.items() if s == slot)

    def _open_slots(self) -> list[int]:
        return [i for i, o in enumerate(self._open) if o]

    def _occupied(self, exclude: int | None = None) -> set[int]:
        return {s for p, s in self._slot.items() if s is not None and p != exclude}

    def _by_connect_rule(self, pad: int) -> int | None:
        opened = self._open_slots()
        if not opened:
            return None
        busy = self._occupied(exclude=pad)
        for s in opened:
            if s not in busy:
                return s
        return opened[0]

    def _by_create_rule(self, pad: int, start: int) -> tuple[int | None, bool]:
        """Следующая по кругу свободная, иначе просто следующая открытая; (клетка, занята)."""
        busy = self._occupied(exclude=pad)
        order = [(start + k) % 4 for k in range(1, 5)]
        order = [s for s in order if self._open[s] and s != start]
        if not order:
            return None, False
        for s in order:
            if s not in busy:
                return s, False
        return order[0], True

    def _place(self, pad: int, new: int | None) -> tuple[list[PadMove], list[PadFeedback]]:
        old = self._slot.get(pad)
        self._slot[pad] = new
        moves = [PadMove(pad, old, new)] if old != new else []
        return moves, [PadFeedback(pad, "dots", 0 if new is None else new + 1)]

    def set_slots(
        self, open_slots: Sequence[bool]
    ) -> tuple[list[PadMove], list[PadFeedback]]:
        self._open = [bool(x) for x in open_slots][:4]
        self._open += [False] * (4 - len(self._open))
        moves: list[PadMove] = []
        fb: list[PadFeedback] = []
        for pad in sorted(self._slot):
            cur = self._slot[pad]
            if cur is None:
                new = self._by_connect_rule(pad)
            elif not self._open[cur]:
                new, _ = self._by_create_rule(pad, cur)
            else:
                continue
            m, f = self._place(pad, new)
            moves += m
            fb += f
        return moves, fb

    def connect(self) -> tuple[int, list[PadMove], list[PadFeedback]]:
        pad = next((n for n in range(1, MAX_PADS + 1) if n not in self._slot), None)
        if pad is None:
            raise ValueError("Все четыре геймпада уже подключены")
        self._slot[pad] = None
        moves, fb = self._place(pad, self._by_connect_rule(pad))
        if not moves:
            moves = [PadMove(pad, None, None)]
        return pad, moves, fb

    def disconnect(self, pad: int) -> list[PadMove]:
        if pad not in self._slot:
            return []
        old = self._slot.pop(pad)
        return [PadMove(pad, old, None)]

    def create(self, pad: int) -> tuple[list[PadMove], list[PadFeedback]]:
        cur = self._slot.get(pad)
        if cur is None:
            return [], []
        new, taken = self._by_create_rule(pad, cur)
        if new is None:
            return [], [PadFeedback(pad, "rumble_nowhere")]
        moves, fb = self._place(pad, new)
        if taken:
            fb.insert(0, PadFeedback(pad, "rumble_taken"))
        return moves, fb
