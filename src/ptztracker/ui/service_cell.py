"""Служебные клетки сетки: положение камер, графики PTZ, сводка. Только рисуют снимки состояния."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Literal

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ptztracker.camera.session import PtzPosition
from ptztracker.ui.theme import FONT_FAMILY, FS_CAPTION, SLOT_COLORS, C, qcolor

Kind = Literal["position", "graphs", "summary"]
KINDS: tuple[Kind, ...] = ("position", "graphs", "summary")
GRAPH_SECONDS = 30.0
TITLES = {"position": "Положение камер", "graphs": "Графики PTZ", "summary": "Сводка"}
GRAPHS_NOTE = "Запись видео — в следующей версии"


@dataclass(frozen=True)
class ServiceRow:
    slot: int
    name: str
    ptz: PtzPosition | None
    history: list[PtzPosition] = field(default_factory=list)
    analysis_fps: float | None = None
    online: bool = False
    pads: list[int] = field(default_factory=list)


def ptz_text(ptz: PtzPosition | None) -> str:
    if ptz is None:
        return "P —  T —  Z —"
    zoom = f"{ptz.zoom_x:.1f}×" if ptz.zoom_x is not None else "—"
    return f"P {ptz.pan_deg:+06.1f}°  T {ptz.tilt_deg:+06.1f}°  Z {zoom}"


def slot_color(slot: int) -> str:
    return SLOT_COLORS[slot % len(SLOT_COLORS)]


class ServiceCell(QWidget):
    def __init__(self, kind: Kind, parent: QWidget | None = None):
        super().__init__(parent)
        self.kind: Kind = kind
        self._rows: list[ServiceRow] = []
        self._selected: int | None = None
        self.setMinimumSize(120, 90)

    def update_view(self, rows: list[ServiceRow], selected: int | None) -> None:
        self._rows = list(rows)
        self._selected = selected
        self.update()

    # --- рисование -----------------------------------------------------

    def _font(self, px: int, bold: bool = False) -> QFont:
        f = QFont(FONT_FAMILY)
        f.setPixelSize(px)
        f.setBold(bold)
        return f

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), qcolor(C.SURFACE))
        p.setPen(qcolor(C.TEXT_DIM))
        p.setFont(self._font(FS_CAPTION, True))
        p.drawText(QRectF(8, 4, self.width() - 16, 18), Qt.AlignLeft | Qt.AlignVCenter, TITLES[self.kind])
        body = QRectF(8, 26, self.width() - 16, self.height() - 34)
        if self.kind == "position":
            self._paint_position(p, body)
        elif self.kind == "graphs":
            self._paint_graphs(p, body)
        else:
            self._paint_summary(p, body)
        p.end()

    def _paint_position(self, p: QPainter, body: QRectF) -> None:
        line_h = 18.0
        p.setFont(self._font(FS_CAPTION))
        for i, r in enumerate(self._rows):
            y = body.top() + i * line_h
            p.setPen(qcolor(slot_color(r.slot)))
            p.drawText(QRectF(body.left(), y, 18, line_h), Qt.AlignLeft | Qt.AlignVCenter, str(r.slot + 1))
            p.setPen(qcolor(C.TEXT if r.slot == self._selected else C.TEXT_DIM))
            p.drawText(QRectF(body.left() + 18, y, body.width() - 18, line_h),
                       Qt.AlignLeft | Qt.AlignVCenter, ptz_text(r.ptz))
        top = QRectF(body.left(), body.top() + len(self._rows) * line_h + 4,
                     body.width(), body.height() - len(self._rows) * line_h - 4)
        side = min(top.width(), top.height())
        if side < 30:
            return
        c = QPointF(top.center().x(), top.center().y())
        rad = side / 2 - 4
        p.setPen(QPen(qcolor(C.LINE), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, rad, rad)
        p.drawEllipse(c, rad / 2, rad / 2)
        p.setPen(qcolor(C.TEXT_FAINT))
        p.drawText(QRectF(c.x() - 20, c.y() - rad - 2, 40, 12), Qt.AlignCenter, "0°")
        for r in self._rows:
            if r.ptz is None:
                continue
            a = math.radians(r.ptz.pan_deg)  # 0° — вверх, плюс — вправо
            end = QPointF(c.x() + rad * math.sin(a), c.y() - rad * math.cos(a))
            col = qcolor(slot_color(r.slot))
            p.setPen(QPen(col, 3 if r.slot == self._selected else 2))
            p.drawLine(c, end)
            p.setBrush(col)
            p.drawEllipse(end, 8, 8)
            p.setPen(qcolor(C.BG))
            p.setFont(self._font(FS_CAPTION, True))
            p.drawText(QRectF(end.x() - 8, end.y() - 8, 16, 16), Qt.AlignCenter, str(r.slot + 1))
            p.setBrush(Qt.NoBrush)

    def _paint_graphs(self, p: QPainter, body: QRectF) -> None:
        row = next((r for r in self._rows if r.slot == self._selected), None)
        note_h = 16.0
        plot = QRectF(body.left(), body.top(), body.width(), body.height() - note_h - 4)
        p.setPen(QPen(qcolor(C.LINE), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(plot)
        if row is not None and row.history and plot.height() > 20:
            now = time.monotonic()
            series = (
                (C.ACCENT, [h.pan_deg for h in row.history], "P"),
                (C.PVW, [h.tilt_deg for h in row.history], "T"),
                (C.LOST, [h.zoom_x if h.zoom_x is not None else math.nan for h in row.history], "Z"),
            )
            xs = [1.0 - (now - h.t) / GRAPH_SECONDS for h in row.history]
            for color, ys, label in series:
                good = [y for y in ys if not math.isnan(y)]
                if not good:
                    continue
                lo, hi = min(good), max(good)
                span = (hi - lo) or 1.0
                pts = [
                    QPointF(plot.left() + max(0.0, min(1.0, x)) * plot.width(),
                            plot.bottom() - 4 - (y - lo) / span * (plot.height() - 8))
                    for x, y in zip(xs, ys, strict=True) if not math.isnan(y)
                ]
                p.setPen(QPen(qcolor(color), 1.5))
                if len(pts) > 1:
                    p.drawPolyline(pts)
                p.setFont(self._font(FS_CAPTION))
                p.drawText(QRectF(plot.left() + 4, plot.top() + 2 + "PTZ".index(label) * 12, 60, 12),
                           Qt.AlignLeft | Qt.AlignVCenter, f"{label} {good[-1]:.1f}")
        p.setPen(qcolor(C.TEXT_FAINT))
        p.setFont(self._font(FS_CAPTION))
        p.drawText(QRectF(body.left(), body.bottom() - note_h, body.width(), note_h),
                   Qt.AlignLeft | Qt.AlignVCenter, GRAPHS_NOTE)

    def _paint_summary(self, p: QPainter, body: QRectF) -> None:
        line_h = 22.0
        p.setFont(self._font(FS_CAPTION))
        for i, r in enumerate(self._rows):
            y = body.top() + i * line_h
            p.setPen(qcolor(slot_color(r.slot)))
            p.drawText(QRectF(body.left(), y, 18, line_h), Qt.AlignLeft | Qt.AlignVCenter, str(r.slot + 1))
            p.setPen(qcolor(C.PVW if r.online else C.FAULT))
            p.drawText(QRectF(body.left() + 18, y, 70, line_h), Qt.AlignLeft | Qt.AlignVCenter,
                       "связь есть" if r.online else "нет связи")
            fps = f"анализ {r.analysis_fps:.0f} к/с" if r.analysis_fps is not None else "анализа нет"
            pads = "геймпад " + ",".join(str(n + 1) for n in r.pads) if r.pads else "геймпада нет"
            p.setPen(qcolor(C.TEXT_DIM))
            p.drawText(QRectF(body.left() + 92, y, body.width() - 92, line_h),
                       Qt.AlignLeft | Qt.AlignVCenter, f"{fps} · {pads}")
