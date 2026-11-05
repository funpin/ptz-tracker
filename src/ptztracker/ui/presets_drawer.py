"""Выдвижная панель «Пресеты» (спека 2026-11-05, вариант А): под сеткой камер, над строкой состояния.

Строка на камеру, выбранная — первой; в свёрнутом виде видна одна строка, за верхний край панель тянется
вверх — добавляются строки других камер. Карточка: снимок кадра и номер; последний вызванный — лаймовая
рамка. Клик — вызвать, Shift+клик — записать, правый клик — записать или стереть снимок. Имён у пресетов
нет: пользователь 2026-11-05 — снимок и так показывает, что это за кадр.
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
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ptztracker.camera.preset_thumbs import PresetThumb
from ptztracker.ui.theme import FS_CAPTION, R_PANEL, R_TAG, SP, C, _rgba, qcolor
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
        self.last = last
        self.setToolTip(f"Пресет {self.n}\nКлик — вызвать, Shift+клик — записать")
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
            p.drawText(r, Qt.AlignCenter, f"{self.n}\nShift+клик")
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
        p.end()


class _WheelScroll(QScrollArea):
    """Строка карточек: колесо мыши листает вбок (полосы прокрутки нет)."""

    def wheelEvent(self, e):  # noqa: N802
        d = e.angleDelta()
        step = d.x() if abs(d.x()) > abs(d.y()) else d.y()
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - step)
        e.accept()


class PresetRow(QWidget):
    CARD_GAP = SP[1] - 1
    HEAD_W = 44
    EDGE_W = 20

    def __init__(self, drawer: PresetsDrawer, parent: QWidget | None = None):
        super().__init__(parent)
        self.drawer = drawer
        self.slot = -1
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP[0], SP[0], SP[0], SP[0])
        lay.setSpacing(SP[1])
        # слева — номер клетки, как в шапке камеры (у выбранной — белый); имя — в подсказке; эфир — под номером
        head_box = QWidget()
        head_box.setFixedWidth(self.HEAD_W)
        head_box.setStyleSheet("background: transparent;")
        head = QVBoxLayout(head_box)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SP[0])
        self.number = QLabel()
        self.number.setFixedSize(22, 22)
        self.number.setAlignment(Qt.AlignCenter)
        self.tag = StateTag("pgm")
        head.addStretch(1)
        head.addWidget(self.number, 0, Qt.AlignHCenter)
        head.addWidget(self.tag, 0, Qt.AlignHCenter)
        head.addStretch(1)
        lay.addWidget(head_box)
        strip = QWidget()
        strip.setStyleSheet("background: transparent;")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(self.CARD_GAP)
        self.cards = [PresetCard(n + 1) for n in range(SLOTS)]
        for c in self.cards:
            c.clicked.connect(lambda _=False, c=c: self._clicked(c.n))
            c.customContextMenuRequested.connect(lambda pos, c=c: self._menu(c, pos))
            sl.addWidget(c)
        sl.addStretch(1)
        # без полосы прокрутки: листается колесом мыши; «‹ ›» у краёв — если есть ещё карточки
        self.scroll = _WheelScroll()
        self.scroll.setWidget(strip)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        # общий стиль красит любой QWidget в фон окна — область прокрутки была бы тёмным прямоугольником в строке
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.scroll.viewport().setStyleSheet("background: transparent;")
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(CARD_H + 4)
        self.more_right = self._edge()
        bar = self.scroll.horizontalScrollBar()
        bar.rangeChanged.connect(lambda *_: self._sync_edges())
        bar.valueChanged.connect(lambda *_: self._sync_edges())
        lay.addWidget(self.scroll)
        lay.addWidget(self.more_right)
        lay.addStretch(1)
        self._lay = lay
        self._sync_edges()

    def resizeEvent(self, e):  # noqa: N802
        """В строке только целые карточки: ширина ленты — кратна карточке, «›» сразу за последней."""
        super().resizeEvent(e)
        m = self._lay.contentsMargins()
        fixed = m.left() + m.right() + self.HEAD_W + self.EDGE_W + 2 * self._lay.spacing()
        step = CARD_W + self.CARD_GAP
        n = max(1, min(SLOTS, (self.width() - fixed + self.CARD_GAP) // step))
        self.scroll.setFixedWidth(n * step - self.CARD_GAP)

    def _edge(self) -> QToolButton:
        """Одна стрелка справа: «›» — дальше; в конце ленты — «‹», назад к началу."""
        b = QToolButton()
        b.setFixedSize(self.EDGE_W, CARD_H)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(f"QToolButton {{ border: none; border-radius: 6px; color: {C.TEXT_DIM}; font-size: 18px;"
                        f" background: transparent; }} QToolButton:hover {{ background: {C.RAISED}; }}")
        b.clicked.connect(self._page)
        return b

    def _page(self) -> None:
        bar = self.scroll.horizontalScrollBar()
        bar.setValue(0 if bar.value() >= bar.maximum() else bar.value() + self.scroll.width() + self.CARD_GAP)

    def _sync_edges(self) -> None:
        bar = self.scroll.horizontalScrollBar()  # стрелка держит место, чтобы лента не прыгала
        b = self.more_right
        b.setEnabled(bar.maximum() > 0)
        b.setText("" if bar.maximum() == 0 else "‹" if bar.value() >= bar.maximum() else "›")
        b.setToolTip("К началу" if b.text() == "‹" else "Дальше")

    def set(self, row: DrawerRow, selected: bool) -> None:
        self.slot = row.slot
        self.number.setText(str(row.slot + 1))
        self.number.setToolTip(f"Камера {row.slot + 1}: {row.name}")
        self.number.setStyleSheet(
            f"background: {C.TEXT if selected else C.RAISED_HI}; color: {C.BG if selected else C.TEXT};"
            f" border-radius: {R_TAG + 1}px; font-weight: 700; font-size: {FS_CAPTION}px;")
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
        m.addAction("Записать сюда текущее положение…", lambda: self.drawer.storeRequested.emit(self.slot, card.n))
        m.addAction("Стереть снимок", lambda: self.drawer.eraseRequested.emit(self.slot, card.n))
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
    eraseRequested = Signal(int, int)
    rowsShownChanged = Signal(int)
    speedChanged = Signal(int)  # скорость переезда в пресет 1…30 — для выбранной камеры (первой строки)
    ROW_H = CARD_H + 12  # строка: карточки и отступы

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        # карточка, как клетки камер: скруглённая, с рамкой
        self.setStyleSheet(f"PresetsDrawer {{ background: {C.SURFACE}; border: 1px solid {C.LINE};"
                           f" border-radius: {R_PANEL}px; }}")
        self.rows: list[DrawerRow] = []
        self.row_widgets: list[PresetRow] = []
        self.rows_shown = 1
        self.grip = _Grip(self)
        title = QLabel("<b>Пресеты</b> · клик — вызвать, Shift+клик — записать · потяните за край — другие камеры")
        title.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION - 0.5}px;")
        # скорость переезда в пресет (камера помнит её сама) — для выбранной камеры
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(1, 30)
        self.speed_slider.setFixedWidth(180)
        self.speed_slider.setFocusPolicy(Qt.NoFocus)
        self.speed_slider.setTracking(False)  # команда — по отпусканию
        self.speed_slider.setToolTip("Как быстро камера переезжает в пресет (выбранная камера)")
        self.speed_slider.valueChanged.connect(self.speedChanged)
        head = QWidget()
        head.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(SP[1])
        hl.addWidget(title, 1)
        speed = QLabel("Скорость переезда")
        speed.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION - 0.5}px;")
        hl.addWidget(speed)
        hl.addSpacing(SP[1])
        slow = QLabel("Медленно")
        slow.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION - 0.5}px;")
        hl.addWidget(slow)
        hl.addWidget(self.speed_slider)
        fast = QLabel("Быстро")
        fast.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION - 0.5}px;")
        hl.addWidget(fast)
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

    def set_speed(self, n: int | None) -> None:
        """Показать скорость выбранной камеры без команды; None — камера ещё не ответила."""
        self.speed_slider.setEnabled(n is not None)
        if n is not None and not self.speed_slider.isSliderDown() and n != self.speed_slider.value():
            self.speed_slider.blockSignals(True)
            self.speed_slider.setValue(n)
            self.speed_slider.blockSignals(False)

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
