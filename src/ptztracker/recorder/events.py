"""Виды событий записи и перевод объектов программы в JSON."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import numpy as np

from ptztracker.video.rtsp import redact_url

if TYPE_CHECKING:
    from ptztracker.vision.tracker import Track

# analysis — результат анализа кадра; control — такт регулятора; cmd — отправленная команда;
# position — фактическое положение камеры (#APC/#GZ); follow — вкл/выкл слежения;
# target — выбор и сброс цели; note — прочее (в том числе ошибки самой записи).
KINDS = {"analysis", "control", "cmd", "position", "follow", "target", "note"}


def track_to_json(tr: Track) -> dict:
    """Человек из трекера → JSON: координаты с точностью 0,1 px, уверенности — 0,01."""
    kp = [[round(float(x), 1), round(float(y), 1), round(float(c), 2)] for x, y, c in tr.keypoints]
    return {
        "id": int(tr.id),
        "box": [round(float(v), 1) for v in tr.box],
        "score": round(float(tr.score), 2),
        "keypoints": kp,
        "velocity": [round(float(v), 1) for v in tr.velocity],
        "hits": int(tr.hits),
        "misses": int(tr.misses),
        "last_seen": float(tr.last_seen),
        "created": float(tr.created),
    }


def redact_meta(value):
    """Копия без паролей: ключи с «password» удаляются, адреса проходят redact_url."""
    if isinstance(value, dict):
        return {k: redact_meta(v) for k, v in value.items() if "password" not in str(k).lower()}
    if isinstance(value, (list, tuple)):
        return [redact_meta(v) for v in value]
    if isinstance(value, str) and "://" in value:
        return redact_url(value)
    return value


def _json_default(obj):
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"{type(obj).__name__} не переводится в JSON")


def event_line(t: float, kind: str, data: dict) -> str:
    """Строка events.jsonl. Бросает TypeError/ValueError, если данные не переводятся в JSON."""
    rec = {"t": float(t), "kind": kind, **data}
    line = json.dumps(rec, ensure_ascii=False, default=_json_default)
    if "://" in line or "password" in line.lower():
        line = json.dumps(redact_meta(json.loads(line)), ensure_ascii=False)
    return line
