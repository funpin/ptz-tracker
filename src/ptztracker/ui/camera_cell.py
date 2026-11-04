"""Клетка сетки камер: заголовок с выбором камеры, видео с оверлеями, значки геймпадов."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ptztracker.ui.preview import PreviewWidget
from ptztracker.ui.theme import SP, C
from ptztracker.ui.widgets import PowerOverlay, StatusPill, VideoFrame

MAX_PADS = 4


class CameraCell(QFrame):
    cameraChosen = Signal(int, object)  # (клетка, id камеры или None)
    selectRequested = Signal(int)

    def __init__(self, slot: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.slot = slot
        self._selected = False
        self.setObjectName("cameraCell")

        self.preview = PreviewWidget()
        self.preview.click_guard = self._click_guard
        self.video_frame = VideoFrame(self.preview)

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

        # заголовок: «N · камера ▾», значки геймпадов
        self.number_label = QLabel(f"{slot + 1} ·")
        self.combo = QComboBox()
        self.combo.setMinimumContentsLength(10)
        self.combo.addItem("нет", None)
        self.combo.currentIndexChanged.connect(self._on_index)
        self.pad_badges = [StatusPill("gamepad") for _ in range(MAX_PADS)]
        for b in self.pad_badges:
            b.hide()
        head = QHBoxLayout()
        head.setContentsMargins(SP[0], SP[0], SP[0], SP[0])
        head.setSpacing(SP[0])
        head.addWidget(self.number_label)
        head.addWidget(self.combo)
        head.addStretch(1)
        for b in self.pad_badges:
            head.addWidget(b)

        self._stack = QStackedLayout()
        self._stack.addWidget(self.video_frame)
        self._service: QWidget | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
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

    def _apply_border(self) -> None:
        color = C.SELECT if self._selected else "transparent"
        self.setStyleSheet(f"QFrame#cameraCell {{ border: 2px solid {color}; border-radius: 8px; }}")

    def set_pads(self, n: int) -> None:
        for i, b in enumerate(self.pad_badges):
            b.setVisible(i < n)

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
