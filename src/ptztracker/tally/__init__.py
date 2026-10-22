"""Tally с видеомикшера Blackmagic ATEM: какой вход сейчас в эфире (PGM) и в превью (PVW). Без Qt."""

from __future__ import annotations

MAX_ATEM_INPUT = 40


def atem_input_of(cam) -> int:
    """Номер входа ATEM у камеры (1…40); всё прочее — 0, «вход не задан»."""
    v = getattr(cam, "atem_input", 0)
    if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_ATEM_INPUT:
        return v
    return 0
