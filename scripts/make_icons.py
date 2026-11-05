"""Иконка приложения из знака (docs/BRANDBOOK.md §2): знак на скруглённом графитовом квадрате.

Запуск на Mac: python scripts/make_icons.py — пишет packaging/icons/ptztracker-1024.png, .ico и .icns.
ICNS собирает iconutil (есть только на macOS); на другой системе .icns не трогается."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
MARK = ROOT / "docs" / "assets" / "logo-mark-dark.svg"
OUT = ROOT / "packaging" / "icons"
GRAPHITE = "#17191D"


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    p.fillPath(tile, QColor(GRAPHITE))
    m = size * 0.06  # знак почти во всю плитку: скобки сами дают поле
    QSvgRenderer(str(MARK)).render(p, QRectF(m, m, size - 2 * m, size - 2 * m))
    p.end()
    return img


def main() -> int:
    QGuiApplication(sys.argv[:1])
    OUT.mkdir(parents=True, exist_ok=True)
    render(1024).save(str(OUT / "ptztracker-1024.png"))
    render(256).save(str(OUT / "ptztracker.ico"))  # Qt пишет ICO; Windows сама уменьшает
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "ptztracker.iconset"
            iconset.mkdir()
            for s in (16, 32, 128, 256, 512):
                render(s).save(str(iconset / f"icon_{s}x{s}.png"))
                render(2 * s).save(str(iconset / f"icon_{s}x{s}@2x.png"))
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "ptztracker.icns")], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
