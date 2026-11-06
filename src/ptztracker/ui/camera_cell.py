"""Клетка сетки камер: заголовок с выбором камеры, видео с оверлеями, значки геймпадов."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ptztracker.ui.icons import icon
from ptztracker.ui.preview import PreviewWidget
from ptztracker.ui.theme import CHEVRON, FS_CAPTION, R_PANEL, R_TAG, SP, C, _rgba, qcolor
from ptztracker.ui.widgets import PadBadge, PowerOverlay, StateTag, StatusPill, VideoFrame, battery_level

MAX_PADS = 4
SHOWN_PADS = 2  # больше — первый и «+N»


class _CycleBar(QWidget):
    """Тонкая лаймовая полоска внизу служебной клетки: доля времени до следующей смены вида."""

    def __init__(self):
        super().__init__()
        self.fraction = 0.0
        self.setFixedHeight(2)

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(C.RAISED_HI))
        p.fillRect(0, 0, round(self.width() * self.fraction), self.height(), qcolor(C.LIME))
        p.end()


class CameraCell(QFrame):
    cameraChosen = Signal(int, object)  # (клетка, id камеры или None)
    selectRequested = Signal(int)
    powerClicked = Signal(int)
    viewToggleRequested = Signal(int)  # двойной клик по шапке: «4 камеры» ↔ «1 камера»
    analysisRequested = Signal(int)  # клик по «БЕЗ АНАЛИЗА»: анализ — этой камере

    def __init__(self, slot: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.slot = slot
        self._selected = False
        self._tally: str | None = None
        self.setObjectName("cameraCell")

        self.preview = PreviewWidget()
        self.preview.click_guard = self._click_guard
        self.video_frame = VideoFrame(self.preview)
        self.video_frame.tags_enabled = False  # эфир и слежение — в шапке клетки

        # цель — поверх видео сверху слева
        self.target_label = QLabel("Цель не выбрана")
        self.reset_target_btn = StatusPill("user-x", "", "neutral")
        self.reset_target_btn.set("", "neutral", "Сбросить цель")
        self.skeleton_check = StatusPill("skeleton", "", "neutral")
        self.skeleton_check.set("", "neutral", "Показать скелет (точки тела)")
        self.skeleton_check.setCheckable(True)
        target_bar = QWidget()
        target_bar.setObjectName("targetBar")
        target_bar.setStyleSheet(f"QWidget#targetBar {{ background: {C.SURFACE}; border-radius: 6px; }}")
        trow = QHBoxLayout(target_bar)
        trow.setContentsMargins(SP[1], SP[0], SP[0], SP[0])
        trow.setSpacing(SP[0])
        trow.addWidget(self.target_label)
        trow.addWidget(self.reset_target_btn)
        trow.addWidget(self.skeleton_check)
        self.video_frame.add_overlay(target_bar, "top-left")
        self.target_bar = target_bar
        self.power_overlay = PowerOverlay()
        self.video_frame.add_overlay(self.power_overlay, "fill")

        # шапка: номер | камера ▾ | PGM/PVW, состояние, СИМУЛЯТОР | геймпады | ⏻ | ⋯ (спека «Шапка клетки»)
        self.number_label = QLabel(str(slot + 1))
        self.number_label.setFixedSize(22, 22)
        self.number_label.setAlignment(Qt.AlignCenter)
        self.combo = QComboBox()
        self.combo.setMinimumContentsLength(6)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._empty_title = "нет камеры"
        self.combo.addItem(self._empty_title, None)
        self.combo.currentIndexChanged.connect(self._on_index)
        self.combo.setStyleSheet(
            f"QComboBox {{ background: transparent; border: 1px solid transparent; font-weight: 600;"
            f" min-height: 26px; padding: 0 {SP[1]}px; border-radius: 7px; }}"
            f"QComboBox:hover {{ background: {C.RAISED}; }}"
            "QComboBox::drop-down { border: none; width: 14px; }"
            # шапка чистая, как раньше; галочка ⌄ — только при наведении, что тут можно выбрать камеру
            # «image: none» на Mac возвращает крупную системную стрелку — прячем нулевым размером
            f"QComboBox::down-arrow {{ image: url({CHEVRON}); width: 0px; height: 0px; }}"
            "QComboBox:hover::down-arrow { width: 10px; height: 10px; }")
        self.tally_tag = StateTag("pgm")
        self.state_tag = StateTag("off")
        self.state_tag.installEventFilter(self)  # «БЕЗ АНАЛИЗА» — клик назначает анализ этой камере
        self.sim_tag = StateTag("sim")
        self.pad_badges = [PadBadge() for _ in range(MAX_PADS)]
        self.more_pads = QLabel()
        self.more_pads.setStyleSheet(
            f"color: {C.TEXT_DIM}; border: 1px solid {C.LINE}; border-radius: 7px; padding: 2px 6px;"
            f" font-size: {FS_CAPTION - 0.5}px; font-weight: 600;")
        self.power_btn = self._head_button("power", "Питание камеры")
        self.power_btn.clicked.connect(lambda: self.powerClicked.emit(self.slot))
        self.more_btn = self._head_button("dots", "Камера: меню OSD, поток, калибровка, диагностика…")
        self.more_btn.setMenu(QMenu(self))
        self.more_btn.setPopupMode(QToolButton.InstantPopup)
        self.more_btn.setStyleSheet(self.more_btn.styleSheet() + "QToolButton::menu-indicator { image: none; }")
        for w in (self.tally_tag, self.state_tag, self.sim_tag, self.more_pads, self.power_btn, self.more_btn,
                  *self.pad_badges):
            w.hide()
        head = QHBoxLayout()
        head.setContentsMargins(SP[1], SP[0], SP[0], SP[0])
        head.setSpacing(SP[1] - 2)
        head.addWidget(self.number_label)
        head.addWidget(self.combo, 1)
        for w in (self.tally_tag, self.state_tag, self.sim_tag):
            head.addWidget(w)
        head.addSpacing(SP[1])
        for b in self.pad_badges:
            head.addWidget(b)
        head.addWidget(self.more_pads)
        head.addWidget(self.power_btn)
        head.addWidget(self.more_btn)

        # в углу видео: разрешение · к/с · анализ
        self.info_label = QLabel()
        self.info_label.setStyleSheet(
            f"background: {_rgba(C.BG, 0.85)}; color: {C.TEXT_DIM}; border-radius: 6px; padding: 3px 8px;"
            f" font-size: {FS_CAPTION - 0.5}px;")
        self.info_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.info_label.hide()
        self.info_muted = False  # режим мыши: внизу — его плашка, цифры потока прячутся
        self.video_frame.add_overlay(self.info_label, "bottom-right")

        self._stack = QStackedLayout()
        self._stack.addWidget(self.video_frame)
        self._service: QWidget | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[0], 0, SP[0], SP[0])
        lay.setSpacing(0)
        lay.addLayout(head)
        lay.addLayout(self._stack, 1)
        self.cycle_bar = _CycleBar()  # «По очереди»: сколько осталось до смены вида
        self.cycle_bar.hide()
        lay.addWidget(self.cycle_bar)
        self._apply_border()

    # --- выбор ----------------------------------------------------------

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        # только шапка: клики по видео — выбор цели, их не перехватываем
        if e.button() == Qt.LeftButton and e.position().y() < self._stack.geometry().top():
            self.viewToggleRequested.emit(self.slot)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def eventFilter(self, obj, e):  # noqa: N802
        if (obj is self.state_tag and e.type() == QEvent.MouseButtonPress and e.button() == Qt.LeftButton
                and self.state_tag.kind() == "no_analysis"):
            self.analysisRequested.emit(self.slot)
            return True
        return super().eventFilter(obj, e)

    def _click_guard(self) -> bool:
        if self._selected:
            return True
        self.selectRequested.emit(self.slot)
        return False

    def _on_index(self, _i: int) -> None:
        self.cameraChosen.emit(self.slot, self.combo.currentData())

    def set_choices(self, items: list[tuple[str, str]], current: str | None,
                    services: list[tuple[str, str]] = ()) -> None:
        """services — что можно показать в пустой клетке («svc:summary», …); идут сразу под заголовком."""
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(self._empty_title, None)
        for data, title in services:
            self.combo.addItem(title, data)
        if services and items:
            self.combo.insertSeparator(self.combo.count())
        for cam_id, name in items:
            self.combo.addItem(name, cam_id)
        i = self.combo.findData(current) if current is not None else 0
        self.combo.setCurrentIndex(max(i, 0))
        self.combo.blockSignals(False)

    def set_selected(self, on: bool) -> None:
        self._selected = on
        self._apply_border()

    def _head_button(self, icon_name: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setIcon(icon(icon_name, C.TEXT_DIM, 16))
        b.setToolTip(tip)
        b.setFixedSize(26, 26)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(f"QToolButton {{ border: none; border-radius: 7px; background: transparent; }}"
                        f"QToolButton:hover {{ background: {C.RAISED}; }}")
        return b

    def _apply_border(self) -> None:
        border = C.SELECT if self._selected else C.LINE
        self.setStyleSheet(
            f"QFrame#cameraCell {{ background: {C.SURFACE}; border: 1px solid {border};"
            f" border-radius: {R_PANEL}px; }}")
        # номер клетки — цветом эфира, чтобы PGM/PVW было видно издалека; без эфира у выбранной — белый
        bg, fg = {"pgm": (C.PGM, C.TEXT), "pvw": (C.PVW, C.BG)}.get(
            self._tally or "", (C.TEXT, C.BG) if self._selected else (C.RAISED_HI, C.TEXT))
        self.number_label.setStyleSheet(
            f"background: {bg}; color: {fg};"
            f" border-radius: {R_TAG + 1}px; font-weight: 700; font-size: {FS_CAPTION}px;")

    def set_pads(self, pads: list[tuple]) -> None:
        """Геймпады клетки: (номер, заряд % или None, заряжается[, (ступень, ступеней) или None]).
        До двух — значками, больше — первый и «+N»."""
        shown = pads if len(pads) <= SHOWN_PADS else pads[:1]
        for i, b in enumerate(self.pad_badges):
            if i < len(shown):
                n, pct, chg, *g = shown[i]
                gear = g[0] if g else None
                b.set(n, pct, chg, gear=gear[0] if gear else None, gears=gear[1] if gear else 0)
            b.setVisible(i < len(shown))
        rest = pads[len(shown):]
        self.more_pads.setVisible(bool(rest))
        if rest:
            self.more_pads.setText(f"+{len(rest)}")
            self.more_pads.setToolTip("\n".join(
                f"Геймпад {n}: " + ("заряжается" if chg else
                                    "заряд неизвестен" if (lvl := battery_level(pct)) is None else f"заряд {lvl[0]}")
                for n, pct, chg, *_ in pads))

    def set_header(self, tally: str | None, state: str | None, sim: bool, power: str | None) -> None:
        """tally: pgm|pvw|None; state: on|lost|off|standby|no_analysis|None; power: on|standby|None (нет кнопки)."""
        self.tally_tag.setVisible(tally is not None)
        if tally:
            self.tally_tag.set(tally)
        if tally != self._tally:
            self._tally = tally
            self._apply_border()
        self.state_tag.setVisible(state is not None)
        if state:
            self.state_tag.set(state)
            free = state == "no_analysis"
            self.state_tag.setCursor(Qt.PointingHandCursor if free else Qt.ArrowCursor)
            self.state_tag.setToolTip("Включить анализ на этой камере (YOLO): нажмите" if free else "")
        self.sim_tag.setVisible(sim)
        self.power_btn.setVisible(power is not None)
        self.more_btn.setVisible(power is not None)
        if power is not None and power != getattr(self, "_power_shown", None):
            self._power_shown = power
            self.power_btn.setIcon(icon("power", C.LIME if power == "on" else C.TEXT_FAINT, 16))
            self.power_btn.setToolTip("Камера работает — перевести в режим ожидания…" if power == "on"
                                      else "Камера в режиме ожидания — включить")

    def set_covered(self, on: bool) -> None:
        """Поверх видео открыто меню быстрых настроек: плашки цели и потока прячутся, чтобы не просвечивать."""
        self.target_bar.setVisible(not on)
        self.info_muted = on
        self.info_label.setVisible(bool(self.info_label.text()) and not on)

    def set_info(self, text: str) -> None:
        self.info_label.setText(text)
        self.info_label.setVisible(bool(text) and not self.info_muted)
        if text:
            self.info_label.adjustSize()
            self.video_frame._place_tags()

    def set_empty_title(self, text: str) -> None:
        """Первый пункт списка: «нет камеры» или название служебного вида, который стоит в пустой клетке."""
        if text != self._empty_title:
            self._empty_title = text
            self.combo.setItemText(0, text)

    def set_cycle(self, fraction: float | None) -> None:
        self.cycle_bar.setVisible(fraction is not None)
        if fraction is not None:
            self.cycle_bar.fraction = fraction
            self.cycle_bar.update()

    def set_service(self, widget: QWidget | None) -> None:
        if self._service is not None:
            self._stack.removeWidget(self._service)
            self._service.hide()
            self._service.setParent(None)  # служебным виджетом владеет окно: может отдать его другой клетке
            self._service = None
        if widget is None:
            self._stack.setCurrentWidget(self.video_frame)
            return
        self._service = widget
        self._stack.addWidget(widget)
        self._stack.setCurrentWidget(widget)
