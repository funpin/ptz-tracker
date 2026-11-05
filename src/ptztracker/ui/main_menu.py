"""Меню ☰ в стиле брендбука: группы с подписями, линейные значки, справа — состояние (ATEM на связи, сколько
геймпадов, включён ли агент), клавиша, тумблер или переключатель 1·2·3·4. Без подменю (макет
docs/design/mockups/menu-camera-diag.html).

Строки — QWidgetAction-заместители настоящих QAction окна: окно и тесты работают с прежними действиями
(trigger, isChecked, setVisible, setText), строка лишь показывает их и нажимает."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QAction, QFont, QPainter
from PySide6.QtWidgets import QMenu, QWidget, QWidgetAction

from ptztracker.ui.icons import icon
from ptztracker.ui.theme import FS_CAPTION, FS_TEXT, R_TAG, SP, C, qcolor

ROW_H = 32
HEAD_H = 24
MENU_W = 340
ICON = 16

# цвет плашки состояния справа: лайм — работает, красный — нет связи, остальное — спокойное серое
STATUS_COLORS = {"ok": C.LIME, "fault": C.FAULT, "neutral": C.TEXT_DIM, "off": C.TEXT_FAINT}

Status = Callable[[], "tuple[str, str] | None"]


def _close_all(menu: QMenu) -> None:
    w = menu
    while isinstance(w, QMenu):
        w.close()
        w = w.parentWidget()


class _Header(QWidget):
    def __init__(self, title: str):
        super().__init__()
        self.title = title
        self.setFixedHeight(HEAD_H)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(MENU_W, HEAD_H)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        f = QFont(self.font())
        f.setPixelSize(FS_CAPTION - 1)
        f.setWeight(QFont.DemiBold)
        f.setCapitalization(QFont.AllUppercase)
        f.setLetterSpacing(QFont.PercentageSpacing, 108)
        p.setFont(f)
        p.setPen(qcolor(C.TEXT_FAINT))
        p.drawText(QRectF(SP[2] - 2, 6, self.width() - SP[3], HEAD_H - 6), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        p.end()


class MenuRow(QWidget):
    """Строка меню, которая показывает и нажимает настоящий QAction (source)."""

    def __init__(self, menu: QMenu, proxy: QWidgetAction, source: QAction, icon_name: str | None,
                 status: Status | None = None, choices: list[QAction] | None = None):
        super().__init__()
        self.menu, self.proxy, self.source = menu, proxy, source
        self.icon_name, self.status, self.choices = icon_name, status, choices or []
        self.setMouseTracking(True)
        self.setFixedHeight(ROW_H)
        self._seg_rects: list[tuple[QRectF, QAction]] = []
        source.changed.connect(self._sync)
        self._sync()

    def _sync(self) -> None:
        text, _, key = self.source.text().partition("\t")
        self.text, self.key = text, key
        self.proxy.setText(self.source.text())
        self.proxy.setVisible(self.source.isVisible())
        self.proxy.setEnabled(self.source.isEnabled())
        self.setEnabled(self.source.isEnabled())
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(MENU_W, ROW_H)

    def enterEvent(self, _e) -> None:  # noqa: N802
        self.menu.setActiveAction(self.proxy)
        self.update()

    def leaveEvent(self, _e) -> None:  # noqa: N802
        self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.LeftButton or not self.isEnabled():
            return
        for r, act in self._seg_rects:
            if r.contains(e.position()):
                act.trigger()
                self.update()
                return  # переключатель 1·2·3·4 меню не закрывает: видно, что выбралось
        _close_all(self.menu)
        self.source.trigger()

    def click(self) -> None:
        """Как щелчок мыши по строке (для клавиатуры и тестов)."""
        _close_all(self.menu)
        self.source.trigger()

    def status_text(self) -> tuple[str, str] | None:
        return self.status() if self.status else None

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        hot = self.isEnabled() and (self.underMouse() or self.menu.activeAction() is self.proxy)
        if hot:
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.RAISED_HI))
            p.drawRoundedRect(r, 7, 7)
        x = SP[2] - 2
        fg = C.TEXT if self.isEnabled() else C.TEXT_FAINT
        if self.icon_name:
            pm = icon(self.icon_name, C.TEXT_DIM if self.isEnabled() else C.GREY, ICON).pixmap(ICON, ICON)
            p.drawPixmap(int(x), int((ROW_H - ICON) / 2), pm)
            x += ICON + SP[2] - 2
        f = QFont(self.font())
        f.setPixelSize(FS_TEXT)
        p.setFont(f)
        right = r.right() - SP[2] + 2
        self._seg_rects = []
        small = QFont(f)
        small.setPixelSize(FS_CAPTION - 1)
        if self.choices:  # переключатель 1·2·3·4
            p.setFont(small)
            cw, ch = 24.0, 22.0
            left = right - cw * len(self.choices)
            box = QRectF(left, (ROW_H - ch) / 2, cw * len(self.choices), ch)
            p.setPen(qcolor(C.LINE))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box, 6, 6)
            for i, act in enumerate(self.choices):
                cell = QRectF(left + i * cw, box.top(), cw, ch)
                if act.isChecked():
                    p.setPen(Qt.NoPen)
                    p.setBrush(qcolor(C.TEXT))
                    p.drawRoundedRect(cell.adjusted(1, 1, -1, -1), 5, 5)
                p.setPen(qcolor(C.BG if act.isChecked() else C.TEXT_DIM))
                p.drawText(cell, Qt.AlignCenter, act.text())
                self._seg_rects.append((cell, act))
            right = left - SP[1]
        elif self.source.isCheckable():  # тумблер
            tw, th = 30.0, 18.0
            t = QRectF(right - tw, (ROW_H - th) / 2, tw, th)
            on = self.source.isChecked()
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.LIME if on else C.RAISED_HI))
            p.drawRoundedRect(t, th / 2, th / 2)
            p.setBrush(qcolor(C.BG if on else C.GREY))
            p.drawEllipse(QRectF(t.right() - 16 if on else t.left() + 2, t.top() + 2, 14, 14))
            right = t.left() - SP[1]
        else:
            st = self.status_text()
            chip = st[0] if st else self.key
            if chip:
                kind = st[1] if st else "off"
                p.setFont(small)
                w = p.fontMetrics().horizontalAdvance(chip) + 12
                c = QRectF(right - w, (ROW_H - 18) / 2, w, 18)
                col = STATUS_COLORS.get(kind, C.TEXT_DIM)
                p.setPen(qcolor(col if kind in ("ok", "fault") else C.LINE))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(c.adjusted(0.5, 0.5, -0.5, -0.5), R_TAG, R_TAG)
                p.setPen(qcolor(col))
                p.drawText(c, Qt.AlignCenter, chip)
                right = c.left() - SP[1]
        p.setFont(f)
        p.setPen(qcolor(fg))
        p.drawText(QRectF(x, 0, right - x, ROW_H), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.text, Qt.ElideRight, int(right - x)))
        p.end()


class MainMenu:
    """Собирает ☰ из групп. add_* возвращают заместитель (QWidgetAction) — он и лежит в меню."""

    def __init__(self, menu: QMenu):
        self.menu = menu
        self.rows: dict[str, MenuRow] = {}
        menu.setMinimumWidth(MENU_W + 12)
        menu.aboutToShow.connect(self.refresh)
        menu.triggered.connect(self._keyboard)  # Enter на строке-виджете: QMenu шлёт triggered заместителя

    def section(self, title: str, first: bool = False) -> QWidgetAction:
        if not first:
            self.menu.addSeparator()
        a = QWidgetAction(self.menu)
        a.setText(title)
        a.setDefaultWidget(_Header(title))
        a.setEnabled(False)
        self.menu.addAction(a)
        return a

    def add(self, source: QAction, icon_name: str | None = None, status: Status | None = None,
            choices: list[QAction] | None = None) -> QWidgetAction:
        a = QWidgetAction(self.menu)
        row = MenuRow(self.menu, a, source, icon_name, status, choices)
        a.setDefaultWidget(row)
        a.setData(row)
        self.menu.addAction(a)
        row._sync()  # setDefaultWidget делает заместителя видимым: вернуть видимость настоящего действия
        self.rows[source.text().partition("\t")[0]] = row
        return a

    def _keyboard(self, a: QAction) -> None:
        row = a.data() if isinstance(a, QWidgetAction) else None
        if isinstance(row, MenuRow) and not row.choices:
            row.source.trigger()

    def refresh(self) -> None:
        for row in self.rows.values():
            row._sync()
