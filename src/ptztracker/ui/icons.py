"""Иконки окна: свои SVG в ui/icons (обводка currentColor), перекрашиваются под состояние."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_DIR = Path(__file__).with_name("icons")


@lru_cache(maxsize=256)
def icon(name: str, color: str, size: int = 18) -> QIcon:
    svg = (_DIR / f"{name}.svg").read_text(encoding="utf-8").replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    out = QIcon()
    for scale in (1, 2):  # обычный и Retina-экран
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        pm.setDevicePixelRatio(scale)
        out.addPixmap(pm)
    return out
