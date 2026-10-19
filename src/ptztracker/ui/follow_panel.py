from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from ptztracker.control.composition import COMPOSITIONS

_ON_STYLE = "QPushButton { background: #16a34a; color: white; font-weight: bold; font-size: 15px; padding: 10px; }"
_OFF_STYLE = "QPushButton { font-weight: bold; font-size: 15px; padding: 10px; }"


class FollowPanel(QGroupBox):
    """Включение слежения, композиция кадра и характер движения."""

    followRequested = Signal(bool)  # оператор нажал кнопку: True — включить
    compositionChanged = Signal(str)
    effortChanged = Signal(float)  # 0..1, по отпусканию ползунка

    def __init__(self, parent=None):
        super().__init__("Слежение", parent)
        self.follow_btn = QPushButton()
        self.follow_btn.setCheckable(True)
        self.follow_btn.setFocusPolicy(Qt.NoFocus)  # пробел обрабатывает окно, а не кнопка
        self.follow_btn.clicked.connect(lambda checked: self.followRequested.emit(checked))

        self.comp_group = QButtonGroup(self)
        self.comp_group.setExclusive(True)
        self.comp_buttons: dict[str, QPushButton] = {}
        grid = QGridLayout()
        for i, (key, comp) in enumerate(COMPOSITIONS.items()):
            b = QPushButton(f"{i + 1} · {comp.title}")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self.compositionChanged.emit(k))
            self.comp_group.addButton(b)
            self.comp_buttons[key] = b
            grid.addWidget(b, i // 2, i % 2)

        self.effort_slider = QSlider(Qt.Horizontal)
        self.effort_slider.setRange(0, 100)
        self.effort_slider.setFocusPolicy(Qt.NoFocus)
        # любое изменение (перетаскивание, клик по полосе, колесо) применяется сразу —
        # иначе ползунок показывал бы не то, что действует
        self.effort_slider.valueChanged.connect(lambda v: self.effortChanged.emit(v / 100))
        erow = QHBoxLayout()
        erow.addWidget(QLabel("Мягко"))
        erow.addWidget(self.effort_slider, 1)
        erow.addWidget(QLabel("Резко"))

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.calib_label = QLabel("")
        self.calib_label.setWordWrap(True)
        self.calib_label.setStyleSheet("color: #f59e0b;")

        lay = QVBoxLayout(self)
        lay.addWidget(self.follow_btn)
        lay.addLayout(grid)
        lay.addLayout(erow)
        lay.addWidget(self.status_label)
        lay.addWidget(self.calib_label)
        self.set_state(False, None)

    def set_state(self, enabled: bool, reason: str | None) -> None:
        self.follow_btn.setChecked(enabled)
        self.follow_btn.setText("● СЛЕЖЕНИЕ ВКЛ  (пробел)" if enabled else "СЛЕЖЕНИЕ ВЫКЛ  (пробел)")
        self.follow_btn.setStyleSheet(_ON_STYLE if enabled else _OFF_STYLE)
        if enabled:
            self.status_label.setText("Камера ведёт цель. Любое ручное действие или Esc выключат слежение.")
        elif reason:
            self.status_label.setText(f"Выключено: {reason}")
        else:
            self.status_label.setText("")

    def show_hint(self, text: str) -> None:
        self.status_label.setText(text)

    def set_composition(self, key: str) -> None:
        if key in self.comp_buttons:
            self.comp_buttons[key].setChecked(True)

    def set_effort(self, x: float) -> None:
        self.effort_slider.blockSignals(True)
        self.effort_slider.setValue(round(x * 100))
        self.effort_slider.blockSignals(False)

    def set_calibrated(self, calibrated: bool) -> None:
        self.calib_label.setText(
            "" if calibrated else "Камера не откалибрована — Сервис → Калибровка (2 мин). Работаю по средним значениям."
        )
