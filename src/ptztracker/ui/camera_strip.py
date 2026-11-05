"""Полоска остальных камер под большой в режиме «1 камера»: снимок кадра, номер цветом эфира, имя.
Клик — эта камера становится большой (макет docs/design/mockups/grid-modes.html)."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPen
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


def summary_clock_text(row) -> str:
    """Время в эфире / слежения, а без времени — состояние словом («вручную», «ожидание»), а не прочерк."""
    from ptztracker.ui.service_cell import ServiceCell

    kind, sec = row.clock
    return ServiceCell._mmss(sec) if sec is not None else ServiceCell.CLOCK_TEXT.get(kind, kind)


def position_text(row) -> str:
    """«−12° · ×1,5» — куда смотрит и насколько крупно."""
    if row.ptz is None:
        return "нет данных"
    zx = row.ptz.zoom_x or 1.0
    return f"{row.ptz.pan_deg:+.0f}° · ×{zx:.1f}".replace("-", "−").replace("+0°", "0°").replace(".", ",")


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
        """Мини-вид: «Сводка» — дорожка и часы на камеру; «Положение» — маленькие круги с конусом и подписью;
        «Графики» — поворот и зум за 30 с строкой на камеру."""
        from ptztracker.ui.service_cell import GRAPH_SECONDS, ServiceCell, position_geometry, tile_colors

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
                           summary_clock_text(row))
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
                p.setPen(Qt.NoPen)
                p.setBrush(qcolor(bg))
                p.drawEllipse(c, 6, 6)
                p.setPen(qcolor(fg))
                p.drawText(QRectF(c.x() - 6, c.y() - 6, 12, 12), Qt.AlignCenter, str(row.slot + 1))
            if len(rows) == 1:  # одна камера — рядом с кругом цифрами: куда смотрит и насколько крупно
                p.setPen(qcolor(C.TEXT_DIM))
                x0 = 8 + d + 10
                p.drawText(QRectF(x0, 22, r.right() - x0 - 6, d), Qt.AlignLeft | Qt.AlignVCenter, position_text(rows[0]))
        elif it.service == "graphs":
            now = time.monotonic()
            lh = min(30.0, (r.height() - 26) / max(1, len(rows)))  # одна камера — графики во всю высоту
            for i, row in enumerate(rows):
                y = 22 + i * lh
                bg, fg = tile_colors(row.tally)
                ts = min(lh - 2, 12.0)
                tile = QRectF(8, y + (lh - ts) / 2, ts, ts)
                p.setPen(Qt.NoPen)
                p.setBrush(qcolor(bg))
                p.drawRoundedRect(tile, 3, 3)
                p.setPen(qcolor(fg))
                p.drawText(tile, Qt.AlignCenter, str(row.slot + 1))
                col = C.LIME if row.track == "on" else C.TEXT_DIM
                w_box = (r.right() - tile.right() - 14) / 2
                for k, get in enumerate((lambda h: h.pan_deg, lambda h: h.zoom_x)):
                    box = QRectF(tile.right() + 5 + k * (w_box + 4), y + 1, w_box, lh - 2)
                    p.setPen(Qt.NoPen)
                    p.setBrush(qcolor(C.RAISED))
                    p.drawRoundedRect(box, 2, 2)
                    pts = [(1.0 - (now - h.t) / GRAPH_SECONDS, get(h)) for h in row.history if get(h) is not None]
                    if len(pts) < 2:
                        continue
                    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
                    span = (hi - lo) or 1.0
                    path = QPainterPath()
                    for j, (x, v) in enumerate(pts):
                        pt = QPointF(box.left() + 2 + max(0.0, min(1.0, x)) * (box.width() - 4),
                                     box.bottom() - 2 - (v - lo) / span * (box.height() - 4))
                        path.moveTo(pt) if j == 0 else path.lineTo(pt)
                    p.setPen(QPen(qcolor(col), 1.4))
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(path)
            p.setPen(qcolor(C.TEXT_FAINT))
            p.drawText(QRectF(8, 4, r.width() - 14, 16), Qt.AlignRight | Qt.AlignVCenter, "поворот · зум")
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
