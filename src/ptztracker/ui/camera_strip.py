"""Полоска остальных камер под большой в режиме «1 камера»: снимок кадра, номер цветом эфира, имя.
Клик — эта камера становится большой (макет docs/design/mockups/grid-modes.html)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QWidget

from ptztracker.ui.theme import FS_CAPTION, R_TAG, SP, C, qcolor

STRIP_H = 64  # высота полоски; карточка камеры — 16:9 по высоте
CARD_W = round(STRIP_H * 16 / 9)
SERVICE_W = 210  # плитка служебного вида шире: в ней мини-сводка или мини-положение
SERVICE_TITLES = {"summary": "Сводка", "position": "Положение", "graphs": "Графики"}


@dataclass
class StripItem:
    slot: int
    name: str
    tally: str | None  # pgm | pvw | None
    image: QImage | None
    service: str | None = None  # плитка служебного вида из свободной клетки: summary | position | graphs
    rows: list = field(default_factory=list)  # ServiceRow — для мини-вида


class StripCard(QWidget):
    clicked = Signal()

    def __init__(self, item: StripItem):
        super().__init__()
        self.item = item
        self.slot = item.slot
        self.setFixedSize(SERVICE_W if item.service else CARD_W, STRIP_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{item.name} — показать крупно" if item.service
                        else f"Камера {item.slot + 1}: {item.name} — сделать большой")

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, e):  # noqa: N802
        if self.item.service:
            self._paint_service()
            return
        it = self.item
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.SURFACE))
        p.drawRoundedRect(r, 6, 6)
        if it.image is not None and not it.image.isNull():
            p.save()
            p.setClipRect(r.adjusted(1, 1, -1, -1))
            p.drawImage(r, it.image)
            p.restore()
        border = {"pgm": C.PGM, "pvw": C.PVW}.get(it.tally or "", C.LINE)
        pen = p.pen()
        pen.setColor(qcolor(border))
        pen.setWidthF(2 if it.tally else 1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 6, 6)
        # номер клетки — как в шапке: цветом эфира
        bg, fg = {"pgm": (C.PGM, C.TEXT), "pvw": (C.PVW, C.BG)}.get(it.tally or "", (C.RAISED_HI, C.TEXT))
        tile = QRectF(5, 5, 18, 18)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(bg))
        p.drawRoundedRect(tile, R_TAG, R_TAG)
        f = p.font()
        f.setPixelSize(FS_CAPTION - 1)
        f.setBold(True)
        p.setFont(f)
        p.setPen(qcolor(fg))
        p.drawText(tile, Qt.AlignCenter, str(it.slot + 1))
        # имя — внизу на тёмной подложке
        f.setBold(False)
        p.setFont(f)
        band = QRectF(r.left(), r.bottom() - 18, r.width(), 18)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.BG, 190))
        p.drawRoundedRect(band, 6, 6)
        p.setPen(qcolor(C.TEXT))
        p.drawText(band.adjusted(6, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(it.name, Qt.ElideRight, int(band.width()) - 10))
        p.end()


    def _paint_service(self) -> None:
        """Мини-вид: «Сводка» — дорожка и часы на камеру; «Положение» — маленькие круги с конусом; «Графики» —
        только название (графики в 64 px не читаются)."""
        from ptztracker.ui.service_cell import ServiceCell, position_geometry, tile_colors

        it = self.item
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(qcolor(C.LINE))
        p.setBrush(qcolor(C.SURFACE))
        p.drawRoundedRect(r, 6, 6)
        f = p.font()
        f.setPixelSize(FS_CAPTION - 1)
        f.setBold(True)
        p.setFont(f)
        p.setPen(qcolor(C.TEXT))
        p.drawText(QRectF(8, 4, r.width() - 12, 16), Qt.AlignLeft | Qt.AlignVCenter, it.name)
        rows = it.rows[:3]
        f.setPixelSize(FS_CAPTION - 2)
        p.setFont(f)
        if it.service == "summary":
            lh = min(13.0, (r.height() - 24) / max(1, len(rows)))
            for i, row in enumerate(rows):
                y = 22 + i * lh
                bg, fg = tile_colors(row.tally)
                tile = QRectF(8, y + 1, lh - 2, lh - 2)
                p.setPen(Qt.NoPen)
                p.setBrush(qcolor(bg))
                p.drawRoundedRect(tile, 3, 3)
                p.setPen(qcolor(fg))
                p.drawText(tile, Qt.AlignCenter, str(row.slot + 1))
                bar = QRectF(tile.right() + 5, y + lh / 2 - 2.5, r.width() - 68 - tile.right(), 5)
                p.setPen(Qt.NoPen)
                p.setBrush(qcolor(C.RAISED))
                p.drawRoundedRect(bar, 2, 2)
                for sg in row.timeline:
                    p.fillRect(QRectF(bar.left() + sg.f0 * bar.width(), bar.top(), (sg.f1 - sg.f0) * bar.width(),
                                      bar.height()), qcolor(ServiceCell.TRACK_COLOR.get(sg.state, C.GREY)))
                kind, sec = row.clock
                p.setPen(qcolor({"air": C.PGM, "on": C.LIME, "lost": C.LOST}.get(kind, C.TEXT_FAINT)))
                p.drawText(QRectF(bar.right() + 4, y, r.right() - bar.right() - 8, lh), Qt.AlignRight | Qt.AlignVCenter,
                           ServiceCell._mmss(sec))
        elif it.service == "position":
            d = r.height() - 30
            for i, row in enumerate(rows):
                c = QPointF(8 + d / 2 + i * (d + 10), 22 + d / 2)
                p.setPen(qcolor(C.LINE))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, d / 2, d / 2)
                g = position_geometry(row)
                if g.has_data:
                    col = C.LIME if row.track == "on" else C.TEXT_DIM
                    a0 = math.radians(g.pan - g.half_fov - 90)
                    a1 = math.radians(g.pan + g.half_fov - 90)
                    rr = d / 2
                    cone = QPainterPath(c)
                    cone.lineTo(c.x() + rr * math.cos(a0), c.y() + rr * math.sin(a0))
                    cone.lineTo(c.x() + rr * math.cos(a1), c.y() + rr * math.sin(a1))
                    cone.closeSubpath()
                    p.setPen(Qt.NoPen)
                    p.setBrush(qcolor(col, 90))
                    p.drawPath(cone)
                bg, fg = tile_colors(row.tally)
                p.setBrush(qcolor(bg))
                p.drawEllipse(c, 6, 6)
                p.setPen(qcolor(fg))
                p.drawText(QRectF(c.x() - 6, c.y() - 6, 12, 12), Qt.AlignCenter, str(row.slot + 1))
        p.end()


class CameraStrip(QWidget):
    slotClicked = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.items: list[StripCard] = []
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, SP[0], 0, 0)
        self._lay.setSpacing(SP[1])
        self._lay.addStretch(1)
        self.setFixedHeight(STRIP_H + SP[0])

    def set_items(self, items: list[StripItem]) -> None:
        if [(c.slot, c.item.service) for c in self.items] == [(it.slot, it.service) for it in items]:
            for card, it in zip(self.items, items, strict=True):  # тот же состав — только свежий кадр
                card.item = it
                card.update()
            return
        for card in self.items:
            self._lay.removeWidget(card)
            card.deleteLater()
        self.items = []
        for i, it in enumerate(items):
            card = StripCard(it)
            card.clicked.connect(lambda s=it.slot: self.slotClicked.emit(s))
            self._lay.insertWidget(i, card)
            self.items.append(card)
