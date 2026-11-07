"""Схема геймпада с подписями для README: рисунок из справки F1 (help_drawings.PadDrawing) и выноски.

Запуск: python scripts/make_pad_map.py — пишет docs/assets/gamepad.png (Retina, 2×). Окно не открывается.
Подписи — те же действия, что в справке (hotkeys_dialog.PAD_ROWS); поменяли кнопки — поправьте CALLOUTS."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication

from ptztracker.ui.help_drawings import PadDrawing
from ptztracker.ui.theme import C, apply_dark_theme, install_russian, qcolor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets" / "gamepad.png"
W, H = 1200, 580
K = 1.2  # масштаб рисунка
PAD_AT = QPoint(int((W - PadDrawing.VIEW[0] * K) / 2), 78)
LEFT_EDGE, RIGHT_EDGE = 250, 950  # где кончаются выноски слева и справа

# (точка на рисунке в координатах PadDrawing.VIEW, сторона, y подписи, заголовок, пояснение)
CALLOUTS = [
    ((139, 43), "left", 118, "Зум шире", "L2"),
    ((140, 69), "left", 162, "Медленнее", "L1 · 8 ступеней скорости"),
    ((187, 107), "left", 218, "Другая камера", "Create"),
    ((131, 163), "left", 282, "Ирис ← →  ·  Gain ↑ ↓", "крестовина"),
    ((200, 230), "left", 360, "Поворот и наклон", "левый стик · нажать — автофокус"),
    ((421, 43), "right", 118, "Зум ближе", "R2"),
    ((420, 69), "right", 162, "Быстрее", "R1"),
    ((373, 107), "right", 218, "Быстрые настройки", "Options"),
    ((420, 162), "right", 282, "Пресеты 1–4", "✕ ○ □ △ · держать 1 с — записать"),
    ((360, 230), "right", 360, "Цель и слежение", "правый стик ← → · нажать — вести"),
    ((280, 131), "top", 30, "Тачпад: свайп — крупность кадра, нажать — стоп камеры", ""),
    ((280, 212), "bottom", 548, "PS — справка", ""),
]


def point(v: tuple[float, float]) -> QPointF:
    return QPointF(PAD_AT.x() + v[0] * K, PAD_AT.y() + v[1] * K)


def main() -> None:
    app = QApplication([])
    install_russian(app)
    apply_dark_theme(app)
    pad = PadDrawing()
    pad.resize(int(PadDrawing.VIEW[0] * K), int(PadDrawing.VIEW[1] * K))
    pad._label = lambda *_a, **_k: None  # свои надписи рисунка (L1, тачпад, PS…) — названия кнопок уже в выносках
    img = QImage(W * 2, H * 2, QImage.Format_ARGB32)
    img.setDevicePixelRatio(2)
    img.fill(qcolor(C.BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    pad.render(p, PAD_AT)
    title, note = QFont(app.font()), QFont(app.font())
    title.setPixelSize(17)
    title.setBold(True)
    note.setPixelSize(13)
    line = QColor(qcolor(C.LIME))
    line.setAlpha(170)
    for v, side, ly, head, sub in CALLOUTS:
        a = point(v)
        if side in ("left", "right"):
            edge = LEFT_EDGE if side == "left" else RIGHT_EDGE
            b = QPointF(edge, ly)
            box = QRectF(0, ly - 12, edge - 14, 24) if side == "left" else QRectF(edge + 14, ly - 12, W - edge - 14, 24)
            align = (Qt.AlignRight if side == "left" else Qt.AlignLeft) | Qt.AlignVCenter
        else:
            b = QPointF(a.x(), ly + (14 if side == "top" else -14))
            box = QRectF(0, ly - 12, W, 24)
            align = Qt.AlignCenter
        p.setPen(QPen(line, 1.5))
        p.drawLine(a, b)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.LIME))
        p.drawEllipse(a, 4, 4)
        p.setFont(title)
        p.setPen(qcolor(C.TEXT))
        p.drawText(box, align, head)
        if sub:
            p.setFont(note)
            p.setPen(qcolor(C.TEXT_DIM))
            p.drawText(box.translated(0, 21), align, sub)
    p.end()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(OUT))
    print(OUT)


if __name__ == "__main__":
    main()
