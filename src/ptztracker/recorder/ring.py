"""Кольцевой буфер по времени: хранит записи не старше `seconds` от самой новой."""

from __future__ import annotations

from collections import deque
from typing import Any


class RingBuffer:
    def __init__(self, seconds: float):
        self.seconds = float(seconds)
        self._items: deque[tuple[float, Any]] = deque()
        self._newest = float("-inf")

    def add(self, t: float, item: Any) -> None:
        self._items.append((t, item))
        self._newest = max(self._newest, t)
        cutoff = self._newest - self.seconds
        while self._items and self._items[0][0] < cutoff:
            self._items.popleft()

    def items(self) -> list[tuple[float, Any]]:
        """Копия содержимого (быстро: копируются только ссылки)."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)
