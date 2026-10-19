"""Чтение сохранённого эпизода (zip чёрного ящика)."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np


class Episode:
    def __init__(self, path: Path, meta: dict, events: list[dict], frame_index: list[dict]):
        self.path = path
        self.meta = meta
        self._events = events
        self._frame_index = frame_index
        times = [e["t"] for e in events] + [f["t"] for f in frame_index]
        self.duration = (max(times) - min(times)) if times else 0.0

    @classmethod
    def open(cls, path: Path | str) -> Episode:
        path = Path(path)
        with zipfile.ZipFile(path) as z:
            meta = json.loads(z.read("meta.json"))
            events = _jsonl(z.read("events.jsonl"))
            frames = _jsonl(z.read("frames.jsonl"))
        return cls(path, meta, events, frames)

    def events(self, kind: str | None = None) -> Iterator[dict]:
        for e in self._events:
            if kind is None or e.get("kind") == kind:
                yield e

    def frames(self) -> Iterator[tuple[float, int, np.ndarray]]:
        """(t_capture, seq, картинка BGR) по порядку записи."""
        with zipfile.ZipFile(self.path) as z:
            for f in self._frame_index:
                data = np.frombuffer(z.read(f["file"]), np.uint8)
                yield f["t"], f["seq"], cv2.imdecode(data, cv2.IMREAD_COLOR)


def _jsonl(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
