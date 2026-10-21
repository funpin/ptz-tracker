"""Чтение сохранённого эпизода (zip чёрного ящика)."""

from __future__ import annotations

import json
import math
import zipfile
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np


class Episode:
    def __init__(self, path: Path, meta: dict, events: list[dict], frame_index: list[dict], bad_lines: int = 0):
        self.path = path
        self.meta = meta
        self._events = events
        self._frame_index = frame_index
        self.bad_lines = bad_lines  # строки events.jsonl, которые не читаются как JSON-объект (пропущены)
        times = [t for t in (_num(e.get("t")) for e in events + frame_index) if t is not None]
        self.duration = (max(times) - min(times)) if times else 0.0

    @classmethod
    def open(cls, path: Path | str) -> Episode:
        path = Path(path)
        with zipfile.ZipFile(path) as z:
            meta = json.loads(z.read("meta.json"))
            events, bad = _jsonl(z.read("events.jsonl"))
            frames, _ = _jsonl(z.read("frames.jsonl"))
        return cls(path, meta, events, frames, bad)

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


def _jsonl(data: bytes) -> tuple[list[dict], int]:
    """Строки JSON-объектов; повреждённые (обрыв записи, мусор) пропускаются и считаются."""
    out, bad = [], 0
    for line in data.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            out.append(obj)
        else:
            bad += 1
    return out, bad


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None
