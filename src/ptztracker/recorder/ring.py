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

    def recent(self, since: float, limit: int, slack: float = 5.0) -> list[tuple[float, Any]]:
        """Не больше `limit` самых новых записей с t > since, по порядку.

        Идёт с конца и останавливается на записях старше since − slack (допуск на неупорядоченные метки).
        """
        out = []
        for t, item in reversed(self._items):
            if t < since - slack or len(out) >= limit:
                break
            if t > since:
                out.append((t, item))
        out.reverse()
        return out

    def items(self) -> list[tuple[float, Any]]:
        """Копия содержимого (быстро: копируются только ссылки)."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)
