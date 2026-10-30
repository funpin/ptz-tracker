from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ptztracker.control.composition import COMPOSITIONS
from ptztracker.ui.state import TrackingView, tracking_view
from ptztracker.ui.theme import FS_CAPTION, SP, C
from ptztracker.ui.widgets import KeyButton, SectionHeader, TrackButton

# короткие подписи кадра для одной строки; полное имя — в подсказке
SHORT_TITLES = {"waist_center": "Пояс", "waist_left": "◧", "waist_right": "◨", "full": "Рост", "wide": "Общ."}


class FollowPanel(QWidget):
    """Композиция кадра и характер движения. Кнопку TRACK (follow_btn) и пояснение (status_label)
    окно ставит в верхнюю панель и поверх видео."""

    followRequested = Signal(bool)  # оператор нажал плитку: True — включить
    compositionChanged = Signal(str)
    effortChanged = Signal(float)  # 0..1, применяется сразу при любом изменении
    calibrateRequested = Signal()  # кнопка «Калибровка…» в плитке при раскачке

    def __init__(self, parent=None):
        super().__init__(parent)
        self.follow_btn = TrackButton()
        self.follow_btn.clicked.connect(lambda checked: self.followRequested.emit(checked))
        self.follow_btn.actionClicked.connect(self.calibrateRequested)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setMaximumWidth(520)
        self.status_label.setStyleSheet(
            f"background: {C.SURFACE}; color: {C.TEXT}; border-radius: 6px; padding: 4px 8px;"
            f" font-size: {FS_CAPTION}px;"
        )

        self.comp_group = QButtonGroup(self)
        self.comp_group.setExclusive(True)
        self.comp_buttons: dict[str, KeyButton] = {}
        grid = QGridLayout()
        grid.setSpacing(SP[0])
        for i, (key, comp) in enumerate(COMPOSITIONS.items()):
            # цифра клавиши — в самой подписи: на узкой кнопке нарисованная клавиша наползала на текст
            b = KeyButton(f"{i + 1} {SHORT_TITLES.get(key, comp.title)}", variant="choice")
            b.setToolTip(f"{comp.title} — клавиша {i + 1}")
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)  # пять в ряд — поровну, без мин. ширины
            b.setStyleSheet("padding: 0 2px;")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self.compositionChanged.emit(k))
            self.comp_group.addButton(b)
            self.comp_buttons[key] = b
            grid.addWidget(b, 0, i)

        self.effort_slider = QSlider(Qt.Horizontal)
        self.effort_slider.setRange(0, 100)
        self.effort_slider.setFocusPolicy(Qt.NoFocus)
        # любое изменение (перетаскивание, клик по полосе, колесо) применяется сразу —
        # иначе ползунок показывал бы не то, что действует
        self.effort_slider.valueChanged.connect(lambda v: self.effortChanged.emit(v / 100))
        erow = QHBoxLayout()
        erow.setSpacing(SP[1])
        erow.addWidget(QLabel("Мягко"))
        erow.addWidget(self.effort_slider, 1)
        erow.addWidget(QLabel("Резко"))

        self.calib_label = QLabel("")
        self.calib_label.setWordWrap(True)
        self.calib_label.setStyleSheet(f"color: {C.LOST}; font-size: {FS_CAPTION}px;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP[1])
        lay.addWidget(SectionHeader("Кадр"))
        lay.addLayout(grid)
        lay.addLayout(erow)
        lay.addWidget(self.calib_label)
        self.set_state(False, None)

    def set_view(self, v: TrackingView) -> None:
        self.follow_btn.set_view(v.kind, v.word, v.why, v.show_calibrate)
        self.follow_btn.setChecked(v.kind in ("on", "lost"))
        self.status_label.setText(v.why)
        self.status_label.setVisible(bool(v.why))
        self.status_label.adjustSize()

    def set_state(self, enabled: bool, reason: str | None) -> None:
        self.set_view(tracking_view(enabled, reason, None, None))

    def show_hint(self, text: str) -> None:
        self.follow_btn.set_why(text)
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))
        self.status_label.adjustSize()

    def set_composition(self, key: str) -> None:
        if key in self.comp_buttons:
            self.comp_buttons[key].setChecked(True)

    def set_effort(self, x: float) -> None:
        self.effort_slider.blockSignals(True)
        self.effort_slider.setValue(round(x * 100))
        self.effort_slider.blockSignals(False)

    def set_calibrated(self, calibrated: bool) -> None:
        self.calib_label.setText(
            "" if calibrated else "Камера не откалибрована: меню ☰ → Калибровка (2 мин)"
        )
        self.calib_label.setVisible(not calibrated)
