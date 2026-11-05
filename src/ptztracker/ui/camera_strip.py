"""Полоска остальных камер под большой в режиме «1 камера»: снимок кадра, номер цветом эфира, имя.
Клик — эта камера становится большой (макет docs/design/mockups/grid-modes.html)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QHBoxLayout, QWidget

from ptztracker.ui.theme import FS_CAPTION, R_TAG, SP, C, qcolor

STRIP_H = 64  # высота полоски; карточка — 16:9 по высоте
CARD_W = round(STRIP_H * 16 / 9)


@dataclass
class StripItem:
    slot: int
    name: str
    tally: str | None  # pgm | pvw | None
    image: QImage | None


class StripCard(QWidget):
    clicked = Signal()

    def __init__(self, item: StripItem):
        super().__init__()
        self.item = item
        self.slot = item.slot
        self.setFixedSize(CARD_W, STRIP_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"Камера {item.slot + 1}: {item.name} — сделать большой")

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, e):  # noqa: N802
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
        if [c.slot for c in self.items] == [it.slot for it in items]:
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
