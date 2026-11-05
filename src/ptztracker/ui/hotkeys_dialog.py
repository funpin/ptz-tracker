"""Окно «Горячие клавиши» (F1): клавиатура и геймпад DualSense. Только справка, камеру не трогает."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

# раскладка — спека 2026-10-30-gamepad-dualsense-design.md; меняя её, правьте gamepad/mapping.py и спеку
GAMEPAD_HELP: list[tuple[str, str]] = [
    ("Левый стик", "поворот и наклон (скорость — по отклонению; чувствительность — ☰ → «Геймпады…»)"),
    ("L3 (нажать левый стик)", "разовая автофокусировка"),
    ("Правый стик ← / →", "цель — сосед слева / справа"),
    ("R3 (нажать правый стик)", "слежение вкл / выкл"),
    ("L2 / R2", "зум шире / ближе"),
    ("R1 / L1", "стик быстрее / медленнее: предел скорости 25–100 % или чувствительность — ☰ → «Геймпады…»"),
    ("Свайп по тачпаду → / ←", "крупность шире / ближе (вправо: пояс → в рост → общий)"),
    ("✕  ○  □  △", "пресеты 1–4: нажать — вызвать, держать 1 с — запомнить"),
    ("Крестовина ← / →", "ирис закрыть / открыть (на «Авто» — снимает авто и сразу делает шаг)"),
    ("Крестовина ↑ / ↓", "gain выше / ниже на 3 dB (на «Авто» — первое нажатие снимает авто: последнее ручное значение или 0 dB)"),
    ("Тачпад (нажать)", "СТОП КАМЕРЫ"),
    ("Options", "меню камеры (в меню: крестовина — пункт и значение, ✕ принять, ○ или △ закрыть)"),
    ("Create (нажать)", "следующая камера"),
    ("Create (держать)", "сохранить момент (как F9)"),
]


def fmt_remaining(seconds: float) -> str:
    """«≈3 ч 10 мин», «≈45 мин»; меньше минуты — «≈1 мин»."""
    mins = max(1, round(seconds / 60))
    h, m = divmod(mins, 60)
    return f"≈{h} ч {m} мин" if h else f"≈{m} мин"


def _table(rows: list[tuple[str, str]], heads: tuple[str, str]) -> QTableWidget:
    t = QTableWidget(len(rows), 2)
    t.setHorizontalHeaderLabels(list(heads))
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.NoSelection)
    t.setFocusPolicy(Qt.NoFocus)
    t.setWordWrap(True)
    for r, (a, b) in enumerate(rows):
        t.setItem(r, 0, QTableWidgetItem(a))
        t.setItem(r, 1, QTableWidgetItem(b))
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    t.resizeRowsToContents()
    return t


class HotkeysDialog(QDialog):
    def __init__(self, keyboard: list[tuple[str, str]] | None = None, parent=None):
        super().__init__(parent)
        from ptztracker.ui.main_window import _HOTKEYS  # поздний импорт: main_window импортирует этот модуль

        self.setWindowTitle("Горячие клавиши")
        self.keys_table = _table([*(keyboard if keyboard is not None else _HOTKEYS), ("F1", "эта справка")],
                                 ("Клавиша", "Действие"))
        self.pad_table = _table(GAMEPAD_HELP, ("Геймпад DualSense", "Действие"))
        lay = QVBoxLayout(self)
        cols = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        left.addWidget(QLabel("Клавиатура"))
        left.addWidget(self.keys_table)
        right.addWidget(QLabel("Геймпад"))
        right.addWidget(self.pad_table)
        cols.addLayout(left, 2)
        cols.addLayout(right, 3)
        lay.addLayout(cols)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(1000, 520)
