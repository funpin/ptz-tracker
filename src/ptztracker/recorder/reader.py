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
        # повреждённые строки events.jsonl и frames.jsonl (не JSON-объект, кадр без файла/номера/времени) — пропущены
        self.bad_lines = bad_lines
        times = [t for t in (_num(e.get("t")) for e in events + frame_index) if t is not None]
        self.duration = (max(times) - min(times)) if times else 0.0

    @classmethod
    def open(cls, path: Path | str) -> Episode:
        path = Path(path)
        with zipfile.ZipFile(path) as z:
            meta = json.loads(z.read("meta.json"))
            events, bad = _jsonl(z.read("events.jsonl"))
            raw_frames, bad_frames = _jsonl(z.read("frames.jsonl"))
        frames = [f for f in raw_frames if _frame_ok(f)]
        return cls(path, meta, events, frames, bad + bad_frames + len(raw_frames) - len(frames))

    @property
    def frame_index(self) -> list[dict]:
        """Кадры записи по порядку: {"t", "seq", "file", ...}; повреждённые строки уже отброшены."""
        return self._frame_index

    def events(self, kind: str | None = None) -> Iterator[dict]:
        for e in self._events:
            if kind is None or e.get("kind") == kind:
                yield e

    def frames(self) -> Iterator[tuple[float, int, np.ndarray]]:
        """(t_capture, seq, картинка BGR) по порядку записи; кадры, которых нет в zip или которые не читаются, пропускаются."""
        with zipfile.ZipFile(self.path) as z:
            for f in self._frame_index:
                img = read_frame(z, f)
                if img is not None:
                    yield float(f["t"]), int(f["seq"]), img


def read_frame(z: zipfile.ZipFile, entry: dict) -> np.ndarray | None:
    """Картинка BGR кадра из открытого zip эпизода; None — файла нет или JPEG не читается."""
    try:
        data = np.frombuffer(z.read(entry["file"]), np.uint8)
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def _frame_ok(f: dict) -> bool:
    try:
        int(f["seq"])
    except (KeyError, TypeError, ValueError, OverflowError):  # seq = inf (1e999 в JSON)
        return False
    return isinstance(f.get("file"), str) and _num(f.get("t")) is not None


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
