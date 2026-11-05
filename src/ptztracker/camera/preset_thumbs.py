"""Снимки пресетов для выдвижной панели: маленькая картинка кадра, имя и поворот камеры.

Лежат в папке данных программы, по камере: <root>/<id камеры>/<n>.jpg и index.json. В журнал для разбора
и в отчёты диагностики не попадают — их код эту папку не читает. Ошибки диска не роняют окно: только лог."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)
THUMB_SIZE = (320, 180)  # 16:9, хватает карточке 150 px на экране с запасом под Retina


@dataclass(frozen=True)
class PresetThumb:
    image: Path | None
    name: str
    pan: float | None  # поворот камеры при записи, ° — точка на «компасе» в «Положении камер»


class PresetThumbs:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, cam_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", cam_id)[:40]
        digest = hashlib.sha1(cam_id.encode()).hexdigest()[:8]  # разные id не сливаются после замены символов
        return self.root / f"{safe}-{digest}"

    def _index(self, cam_id: str) -> dict:
        try:
            data = json.loads((self._dir(cam_id) / "index.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_index(self, cam_id: str, data: dict) -> None:
        d = self._dir(cam_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def load(self, cam_id: str) -> dict[int, PresetThumb]:
        out: dict[int, PresetThumb] = {}
        d = self._dir(cam_id)
        for key, v in self._index(cam_id).items():
            try:
                n = int(key)
            except ValueError:
                continue
            img = d / f"{n}.jpg"
            pan = v.get("pan") if isinstance(v, dict) else None
            out[n] = PresetThumb(img if img.exists() else None, str(v.get("name", "")) if isinstance(v, dict) else "",
                                 float(pan) if isinstance(pan, (int, float)) else None)
        return out

    def save(self, cam_id: str, n: int, image: np.ndarray, pan: float | None) -> None:
        try:
            d = self._dir(cam_id)
            d.mkdir(parents=True, exist_ok=True)
            small = cv2.resize(image, THUMB_SIZE, interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(d / f"{n}.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 85])
            data = self._index(cam_id)
            entry = data.get(str(n)) if isinstance(data.get(str(n)), dict) else {}
            entry["pan"] = pan
            data[str(n)] = entry
            self._write_index(cam_id, data)
        except Exception:  # noqa: BLE001 — снимок пресета не важнее работы окна
            log.exception("Снимок пресета %s не сохранён", n)

    def rename(self, cam_id: str, n: int, name: str) -> None:
        data = self._index(cam_id)
        entry = data.get(str(n)) if isinstance(data.get(str(n)), dict) else {}
        entry["name"] = name.strip()[:24]
        data[str(n)] = entry
        try:
            self._write_index(cam_id, data)
        except OSError:
            log.exception("Имя пресета %s не сохранено", n)

    def erase(self, cam_id: str, n: int) -> None:
        data = self._index(cam_id)
        data.pop(str(n), None)
        try:
            (self._dir(cam_id) / f"{n}.jpg").unlink(missing_ok=True)
            self._write_index(cam_id, data)
        except OSError:
            log.exception("Пресет %s не стёрт", n)
