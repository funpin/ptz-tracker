"""Выдвижная панель «Пресеты» (спека 2026-11-05, вариант А): под сеткой камер, над строкой состояния.

Строка на камеру, выбранная — первой; в свёрнутом виде видна одна строка, за верхний край панель тянется
вверх — добавляются строки других камер. Карточка: снимок кадра, номер, имя; последний вызванный — лаймовая
рамка. Клик — вызвать, Shift+клик — записать, правый клик — переименовать, обновить снимок, стереть.
Панель только показывает и сообщает о действиях: камеру трогает окно."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ptztracker.camera.preset_thumbs import PresetThumb
from ptztracker.ui.theme import FS_CAPTION, R_TAG, SP, C, _rgba, qcolor
from ptztracker.ui.widgets import StateTag

SLOTS = 12  # пресетов на камеру в панели (камера умеет 100; дальше — по мере надобности)
CARD_W, CARD_H = 132, 74


@dataclass
class DrawerRow:
    slot: int
    name: str
    tally: str | None
    thumbs: dict[int, PresetThumb] = field(default_factory=dict)
    last: int | None = None


class PresetCard(QAbstractButton):
    def __init__(self, n: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.n = n
        self.name = ""
        self.last = False
        self._pix: QPixmap | None = None
        self._pix_src: str | None = None
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    def set(self, thumb: PresetThumb | None, last: bool) -> None:
        src = str(thumb.image) if thumb and thumb.image else None
        if src != self._pix_src or (src and thumb.image.stat().st_mtime != getattr(self, "_mtime", None)):
            self._pix_src = src
            self._mtime = thumb.image.stat().st_mtime if src else None
            self._pix = QPixmap(src) if src else None
        self.name = thumb.name if thumb else ""
        self.last = last
        tip = f"Пресет {self.n}" + (f" — {self.name}" if self.name else "")
        self.setToolTip(tip + "\nКлик — вызвать, Shift+клик — записать, правый клик — имя, снимок")
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(CARD_W, CARD_H)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 7, 7)
        if self._pix is not None and not self._pix.isNull():
            p.setClipPath(path)
            p.drawPixmap(r.toRect(), self._pix)
            p.setClipping(False)
            if self.name:
                band = QRectF(r.left(), r.bottom() - 20, r.width(), 20)
                p.save()
                p.setClipPath(path)
                p.fillRect(band, qcolor(C.BG, 200))
                p.restore()
            border = QPen(qcolor(C.LIME if self.last else C.LINE), 2 if self.last else 1)
        else:
            p.setBrush(qcolor(C.BG))
            border = QPen(qcolor(C.LIME if self.last else C.GREY), 2 if self.last else 1,
                          Qt.SolidLine if self.last else Qt.DashLine)
            p.setPen(Qt.NoPen)
            p.drawPath(path)
            f = QFont(self.font())
            f.setPixelSize(FS_CAPTION - 1)
            p.setFont(f)
            p.setPen(qcolor(C.TEXT_FAINT))
            hint = "" if self.name else "\nShift+клик"
            p.drawText(r, Qt.AlignCenter, f"{self.n}{hint}")
        if self.underMouse():
            p.fillPath(path, qcolor(C.TEXT, 18))
        p.setBrush(Qt.NoBrush)
        p.setPen(border)
        p.drawPath(path)
        f = QFont(self.font())
        f.setPixelSize(FS_CAPTION - 1)
        f.setBold(True)
        p.setFont(f)
        badge = QRectF(r.left() + 4, r.top() + 4, 8 + p.fontMetrics().horizontalAdvance(str(self.n)), 16)
        if self._pix is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.BG, 210))
            p.drawRoundedRect(badge, R_TAG - 1, R_TAG - 1)
            p.setPen(qcolor(C.TEXT))
            p.drawText(badge, Qt.AlignCenter, str(self.n))
        if self.name:
            f.setBold(False)
            p.setFont(f)
            p.setPen(qcolor(C.TEXT))
            p.drawText(QRectF(r.left() + 6, r.bottom() - 20, r.width() - 12, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self.name, Qt.ElideRight, int(r.width() - 12)))
        p.end()


class PresetRow(QWidget):
    def __init__(self, drawer: PresetsDrawer, parent: QWidget | None = None):
        super().__init__(parent)
        self.drawer = drawer
        self.slot = -1
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP[0], SP[0], SP[0], SP[0])
        lay.setSpacing(SP[1])
        head = QVBoxLayout()
        head.setSpacing(2)
        self.title = QLabel()
        self.title.setFixedWidth(84)
        self.title.setWordWrap(True)
        self.title.setStyleSheet(f"color: {C.TEXT}; font-weight: 600; font-size: {FS_CAPTION}px;")
        self.tag = StateTag("pgm")
        head.addWidget(self.title)
        head.addWidget(self.tag)
        head.addStretch(1)
        lay.addLayout(head)
        strip = QWidget()
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(SP[1] - 1)
        self.cards = [PresetCard(n + 1) for n in range(SLOTS)]
        for c in self.cards:
            c.clicked.connect(lambda _=False, c=c: self._clicked(c.n))
            c.customContextMenuRequested.connect(lambda pos, c=c: self._menu(c, pos))
            sl.addWidget(c)
        sl.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidget(strip)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(CARD_H + 12)
        lay.addWidget(self.scroll, 1)

    def set(self, row: DrawerRow, selected: bool) -> None:
        self.slot = row.slot
        self.title.setText(f"{row.slot + 1} · {row.name}")
        self.tag.setVisible(row.tally is not None)
        if row.tally:
            self.tag.set(row.tally)
        for c in self.cards:
            c.set(row.thumbs.get(c.n), row.last == c.n)
        self.setAttribute(Qt.WA_StyledBackground, True)
        bg = _rgba(C.TEXT, 0.04) if selected else "transparent"
        self.setStyleSheet(f"PresetRow {{ background: {bg}; border-radius: 8px; }}")

    def _clicked(self, n: int) -> None:
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            self.drawer.storeRequested.emit(self.slot, n)
        else:
            self.drawer.recallRequested.emit(self.slot, n)

    def _menu(self, card: PresetCard, pos) -> None:
        m = QMenu(self)
        m.addAction("Переименовать…", lambda: self.drawer.renameRequested.emit(self.slot, card.n))
        m.addAction("Записать сюда текущее положение…", lambda: self.drawer.storeRequested.emit(self.slot, card.n))
        m.addAction("Стереть снимок и имя", lambda: self.drawer.eraseRequested.emit(self.slot, card.n))
        m.exec(card.mapToGlobal(pos))


class _Grip(QWidget):
    """Верхний край панели: потяните вверх — строки других камер, вниз — обратно."""

    def __init__(self, drawer: PresetsDrawer):
        super().__init__(drawer)
        self.drawer = drawer
        self.setFixedHeight(10)
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip("Потяните вверх — пресеты других камер")
        self._y0: float | None = None
        self._rows0 = 1

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.GREY))
        p.drawRoundedRect(QRectF(self.width() / 2 - 22, 3, 44, 4), 2, 2)
        p.end()

    def mousePressEvent(self, e):  # noqa: N802
        self._y0, self._rows0 = e.position().y(), self.drawer.rows_shown

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._y0 is not None:
            dy = self._y0 - e.position().y()
            self.drawer.set_rows_shown(self._rows0 + round(dy / self.drawer.ROW_H))

    def mouseReleaseEvent(self, e):  # noqa: N802
        self.mouseMoveEvent(e)
        self._y0 = None


class PresetsDrawer(QWidget):
    recallRequested = Signal(int, int)  # клетка, номер пресета 1…
    storeRequested = Signal(int, int)
    renameRequested = Signal(int, int)
    eraseRequested = Signal(int, int)
    rowsShownChanged = Signal(int)
    ROW_H = CARD_H + 20

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"PresetsDrawer {{ background: {C.SURFACE}; border-top: 1px solid {C.LINE}; }}")
        self.rows: list[DrawerRow] = []
        self.row_widgets: list[PresetRow] = []
        self.rows_shown = 1
        self.grip = _Grip(self)
        head = QLabel("<b>Пресеты</b> · клик — вызвать, Shift+клик — записать · тяните край вверх — другие камеры")
        head.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION - 0.5}px;")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(SP[2], 0, SP[2], SP[1])
        self._lay.setSpacing(2)
        self._lay.addWidget(self.grip)
        self._lay.addWidget(head)

    def set_rows(self, rows: list[DrawerRow]) -> None:
        self.rows = list(rows)
        while len(self.row_widgets) < len(rows):
            w = PresetRow(self)
            self.row_widgets.append(w)
            self._lay.addWidget(w)
        for i, w in enumerate(self.row_widgets):
            if i < len(rows):
                w.set(rows[i], i == 0)
        self._apply_rows()

    def set_rows_shown(self, n: int) -> None:
        n = max(1, min(n, max(1, len(self.rows))))
        if n != self.rows_shown:
            self.rows_shown = n
            self._apply_rows()
            self.rowsShownChanged.emit(n)

    def _apply_rows(self) -> None:
        self.rows_shown = max(1, min(self.rows_shown, max(1, len(self.rows))))
        for i, w in enumerate(self.row_widgets):
            w.setVisible(i < min(self.rows_shown, len(self.rows)))

    def card(self, slot: int, n: int) -> PresetCard | None:
        for w in self.row_widgets:
            if w.slot == slot and w.isVisibleTo(self):
                return w.cards[n - 1]
        return None
