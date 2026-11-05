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
    QPushButton,
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


# пресеты 1–4 вызываются кнопками геймпада (gamepad/mapping._PRESET_BUTTONS): × ○ □ △
PAD_SYMBOLS = {1: "×", 2: "○", 3: "□", 4: "△"}


def badge_text(n: int) -> str:
    """«1 ×» — номер пресета и кнопка геймпада, которой он вызывается."""
    return f"{n} {PAD_SYMBOLS[n]}" if n in PAD_SYMBOLS else str(n)


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
        pad = f"\nНа геймпаде — {PAD_SYMBOLS[self.n]} (держать 1 с — записать)" if self.n in PAD_SYMBOLS else ""
        self.setToolTip(f"Пресет {self.n}\nКлик — вызвать, Shift+клик — записать{pad}")
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
            p.drawText(r, Qt.AlignCenter, f"{badge_text(self.n)}\nShift+клик")
        if self.underMouse():
            p.fillPath(path, qcolor(C.TEXT, 18))
        p.setBrush(Qt.NoBrush)
        p.setPen(border)
        p.drawPath(path)
        f = QFont(self.font())
        f.setPixelSize(FS_CAPTION - 1)
        f.setBold(True)
        p.setFont(f)
        text = badge_text(self.n)
        badge = QRectF(r.left() + 4, r.top() + 4, 10 + p.fontMetrics().horizontalAdvance(text), 16)
        if self._pix is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.BG, 210))
            p.drawRoundedRect(badge, R_TAG - 1, R_TAG - 1)
            p.setPen(qcolor(C.TEXT))
            p.drawText(badge, Qt.AlignCenter, text)
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
    HEAD_W = 84
    EDGE_W = 20
    HANDLE_W = 12

    def __init__(self, drawer: PresetsDrawer, parent: QWidget | None = None):
        super().__init__(parent)
        self.drawer = drawer
        self.slot = -1
        self.selected = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP[0], SP[0], SP[0], SP[0])
        lay.setSpacing(SP[1])
        # слева — ⋮⋮ (потянуть — поменять порядок строк), номер клетки (у выбранной — белый), имя и эфир
        self.handle = _Handle(self)
        lay.addWidget(self.handle)
        head_box = QWidget()
        head_box.setFixedWidth(self.HEAD_W)
        head_box.setStyleSheet("background: transparent;")
        head = QVBoxLayout(head_box)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SP[0])
        self.number = QLabel()
        self.number.setFixedSize(22, 22)
        self.number.setAlignment(Qt.AlignCenter)
        self.name_label = QLabel()
        self.name_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION - 1}px; background: transparent;")
        self.tag = StateTag("pgm")
        head.addStretch(1)
        head.addWidget(self.number, 0, Qt.AlignLeft)
        head.addWidget(self.name_label, 0, Qt.AlignLeft)
        head.addWidget(self.tag, 0, Qt.AlignLeft)
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
        fixed = m.left() + m.right() + self.HANDLE_W + self.HEAD_W + self.EDGE_W + 3 * self._lay.spacing()
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
        self.selected = selected
        self.number.setText(str(row.slot + 1))
        self.name_label.setText(self.name_label.fontMetrics().elidedText(row.name, Qt.ElideRight, self.HEAD_W - 4))
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


class _Handle(QLabel):
    """⋮⋮ слева у строки: потяните вверх или вниз — строка встанет на новое место (порядок строк пресетов)."""

    def __init__(self, row: PresetRow):
        super().__init__("⠿")
        self.row = row
        self.setFixedWidth(PresetRow.HANDLE_W)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Потяните вверх или вниз — поменять порядок строк")
        self.setStyleSheet(f"color: {C.GREY}; font-size: 15px; font-weight: 700; background: transparent;")
        self._y0: float | None = None

    def mousePressEvent(self, e):  # noqa: N802
        self._y0 = e.globalPosition().y()
        self.setCursor(Qt.ClosedHandCursor)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._y0 is None:
            return
        steps = round((e.globalPosition().y() - self._y0) / self.row.drawer.ROW_H)
        self._y0 = None
        self.setCursor(Qt.OpenHandCursor)
        if steps:
            d = self.row.drawer
            order = [r.slot for r in d.rows]
            d.move_row(self.row.slot, order.index(self.row.slot) + steps)


class PresetsDrawer(QWidget):
    recallRequested = Signal(int, int)  # клетка, номер пресета 1…
    storeRequested = Signal(int, int)
    eraseRequested = Signal(int, int)
    rowsShownChanged = Signal(int)
    rowsOrderChanged = Signal(list)  # клетки строк сверху вниз — кнопки камер и ⋮⋮
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
        self.chips: dict[int, QPushButton] = {}
        self._shown: list[int] = []
        title = QLabel("<b>Пресеты</b> · строки:")
        title.setToolTip("Клик по карточке — вызвать пресет, Shift+клик — записать. Кнопки камер — какие строки"
                         " показать; ⋮⋮ слева у строки — поменять порядок")
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
        hl.addWidget(title)
        self._chips_lay = QHBoxLayout()
        self._chips_lay.setSpacing(SP[0])
        hl.addLayout(self._chips_lay)
        hl.addStretch(1)
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
        self._lay.setContentsMargins(SP[2], SP[1], SP[2], SP[1])
        self._lay.addWidget(head)

    def set_rows(self, rows: list[DrawerRow], selected: int | None = None) -> None:
        """Строки сверху вниз; selected — клетка выбранной камеры (белый номер), по умолчанию — первая строка."""
        self.rows = list(rows)
        while len(self.row_widgets) < len(rows):
            w = PresetRow(self)
            self.row_widgets.append(w)
            self._lay.addWidget(w)
        sel = selected if selected is not None else (rows[0].slot if rows else None)
        for i, w in enumerate(self.row_widgets):
            if i < len(rows):
                w.set(rows[i], rows[i].slot == sel)
        self.rows_shown = len(rows)
        self._apply_rows()
        self.rowsShownChanged.emit(self.rows_shown)

    def set_cameras(self, cams: list[tuple[int, str]], shown: list[int]) -> None:
        """Кнопки камер в заголовке: нажата — строка этой камеры показана."""
        self._shown = list(shown)
        if [k for k, _n in cams] != list(self.chips) or any(
                self.chips[k].text() != f"{k + 1} {n}" for k, n in cams):
            for b in self.chips.values():
                self._chips_lay.removeWidget(b)
                b.deleteLater()
            self.chips = {}
            for k, name in cams:
                b = QPushButton(f"{k + 1} {name}")
                b.setProperty("variant", "seg")
                b.setCheckable(True)
                b.setFocusPolicy(Qt.NoFocus)
                b.setStyleSheet(
                    f"QPushButton {{ min-height: 22px; padding: 0 {SP[1]}px; font-size: {FS_CAPTION}px;"
                    f" background: transparent; color: {C.TEXT_DIM}; border: 1px solid {C.LINE}; border-radius: 7px; }}"
                    f"QPushButton:hover {{ background: {C.RAISED_HI}; }}"
                    f"QPushButton:checked {{ background: {C.TEXT}; color: {C.BG}; border-color: {C.TEXT};"
                    f" font-weight: 600; }}")
                b.clicked.connect(lambda _=False, k=k: self._toggle(k))
                self.chips[k] = b
                self._chips_lay.addWidget(b)
        for k, b in self.chips.items():
            b.setChecked(k in self._shown)

    def _toggle(self, slot: int) -> None:
        order = list(self._shown)
        if slot in order:
            if len(order) > 1:  # последнюю строку не убрать: панель пресетов без строк бесполезна
                order.remove(slot)
        else:
            order.append(slot)
        for k, b in self.chips.items():
            b.setChecked(k in order)
        self.rowsOrderChanged.emit(order)

    def move_row(self, slot: int, index: int) -> None:
        """Строку клетки slot — на место index (0 — самая верхняя)."""
        order = [r.slot for r in self.rows]
        if slot not in order:
            return
        order.remove(slot)
        order.insert(max(0, min(len(order), index)), slot)
        self.rowsOrderChanged.emit(order)

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
