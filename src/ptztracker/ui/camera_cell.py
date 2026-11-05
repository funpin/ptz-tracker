"""Клетка сетки камер: заголовок с выбором камеры, видео с оверлеями, значки геймпадов."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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
from ptztracker.ui.theme import FS_CAPTION, R_PANEL, R_TAG, SP, C, _rgba
from ptztracker.ui.widgets import PadBadge, PowerOverlay, StateTag, StatusPill, VideoFrame

MAX_PADS = 4
SHOWN_PADS = 2  # больше — первый и «+N»


class CameraCell(QFrame):
    cameraChosen = Signal(int, object)  # (клетка, id камеры или None)
    selectRequested = Signal(int)
    powerClicked = Signal(int)

    def __init__(self, slot: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.slot = slot
        self._selected = False
        self.setObjectName("cameraCell")

        self.preview = PreviewWidget()
        self.preview.click_guard = self._click_guard
        self.video_frame = VideoFrame(self.preview)
        self.video_frame.tags_enabled = False  # эфир и слежение — в шапке клетки

        # цель — поверх видео сверху слева
        self.target_label = QLabel("Цель: не выбрана — кликните по человеку на видео")
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
        self.combo.addItem("нет", None)
        self.combo.currentIndexChanged.connect(self._on_index)
        self.combo.setStyleSheet(
            f"QComboBox {{ background: transparent; border: 1px solid transparent; font-weight: 600;"
            f" min-height: 26px; padding: 0 {SP[1]}px; border-radius: 7px; }}"
            f"QComboBox:hover {{ background: {C.RAISED}; }}"
            "QComboBox::drop-down { border: none; width: 14px; }")
        self.tally_tag = StateTag("pgm")
        self.state_tag = StateTag("off")
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
        self.video_frame.add_overlay(self.info_label, "bottom-right")

        self._stack = QStackedLayout()
        self._stack.addWidget(self.video_frame)
        self._service: QWidget | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[0], 0, SP[0], SP[0])
        lay.setSpacing(0)
        lay.addLayout(head)
        lay.addLayout(self._stack, 1)
        self._apply_border()

    # --- выбор ----------------------------------------------------------

    def _click_guard(self) -> bool:
        if self._selected:
            return True
        self.selectRequested.emit(self.slot)
        return False

    def _on_index(self, _i: int) -> None:
        self.cameraChosen.emit(self.slot, self.combo.currentData())

    def set_choices(self, items: list[tuple[str, str]], current: str | None) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("нет", None)
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
        on = self._selected
        self.number_label.setStyleSheet(
            f"background: {C.TEXT if on else C.RAISED_HI}; color: {C.BG if on else C.TEXT};"
            f" border-radius: {R_TAG + 1}px; font-weight: 700; font-size: {FS_CAPTION}px;")

    def set_pads(self, pads: list[tuple[int, int | None, bool]]) -> None:
        """Геймпады клетки: (номер, заряд % или None, заряжается). До двух — значками, больше — первый и «+N»."""
        shown = pads if len(pads) <= SHOWN_PADS else pads[:1]
        for i, b in enumerate(self.pad_badges):
            if i < len(shown):
                b.set(*shown[i])
            b.setVisible(i < len(shown))
        rest = pads[len(shown):]
        self.more_pads.setVisible(bool(rest))
        if rest:
            self.more_pads.setText(f"+{len(rest)}")
            self.more_pads.setToolTip("\n".join(
                f"Геймпад {n}: " + ("заряд неизвестен" if pct is None else f"{pct}%")
                + (", заряжается" if chg else "") for n, pct, chg in pads))

    def set_header(self, tally: str | None, state: str | None, sim: bool, power: str | None) -> None:
        """tally: pgm|pvw|None; state: on|lost|off|standby|no_analysis|None; power: on|standby|None (нет кнопки)."""
        self.tally_tag.setVisible(tally is not None)
        if tally:
            self.tally_tag.set(tally)
        self.state_tag.setVisible(state is not None)
        if state:
            self.state_tag.set(state)
        self.sim_tag.setVisible(sim)
        self.power_btn.setVisible(power is not None)
        self.more_btn.setVisible(power is not None)
        if power is not None and power != getattr(self, "_power_shown", None):
            self._power_shown = power
            self.power_btn.setIcon(icon("power", C.LIME if power == "on" else C.TEXT_FAINT, 16))
            self.power_btn.setToolTip("Камера работает — перевести в режим ожидания…" if power == "on"
                                      else "Камера в режиме ожидания — включить")

    def set_info(self, text: str) -> None:
        self.info_label.setText(text)
        self.info_label.setVisible(bool(text))
        if text:
            self.info_label.adjustSize()
            self.video_frame._place_tags()

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
