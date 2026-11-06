"""Окно «Справка» (F1): геймпад DualSense, клавиатура и мышь — рисунки с подсветкой. Только справка,
камеру не трогает."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from ptztracker.ui.help_drawings import KeyboardDrawing, MouseDrawing, PadDrawing
from ptztracker.ui.theme import FS_BUTTON, FS_CAPTION, R_BUTTON, R_TAG, SP, C, _rgba
from ptztracker.ui.widgets import BrandMark

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
    ("Options", "быстрые настройки — меню поверх видео (крестовина — пункт и значение, ✕ принять, ○ или △ закрыть)"),
    ("Create (нажать)", "следующая камера"),
    ("Create (держать)", "сохранить момент (как F9)"),
]


def fmt_remaining(seconds: float) -> str:
    """«≈3 ч 10 мин», «≈45 мин»; меньше минуты — «≈1 мин»."""
    mins = max(1, round(seconds / 60))
    h, m = divmod(mins, 60)
    return f"≈{h} ч {m} мин" if h else f"≈{m} мин"


MOUSE_HELP: list[tuple[str, str]] = [
    ("Клик по человеку", "выбрать цель: рамка «ВЕДУ», затем Пробел — слежение"),
    ("Перетащить зону", "при слежении: где держать лицо; за уголок — размер зон; двойной клик — сброс"),
    ("Клик по другой клетке", "выбрать камеру: ей управляют стик, клавиши и правая колонка"),
]


def pad_elements(label: str) -> str | None:
    """Строка таблицы геймпада → элемент рисунка (help_drawings.PadDrawing)."""
    low = label.lower()
    for prefix, key in (("левый стик", "lstick"), ("l3", "l3"), ("правый стик", "rstick"), ("r3", "r3"),
                        ("l2", "l2,r2"), ("r1", "r1,l1"), ("l1", "l1,r1"), ("✕", "cross,circle,square,triangle"),
                        ("крестовина ← ", "dpad_lr"),
                        ("крестовина ↑", "dpad_ud"), ("крестовина", "dpad_lr"), ("тачпад", "touchpad"), ("свайп", "touchpad"),
                        ("options", "options"), ("create", "create")):
        if low.startswith(prefix):
            return key
    return None


_KEY_NAMES = {"пробел": "Space", "←": "Left", "→": "Right", "↑": "Up", "↓": "Down", "−": "−", "-": "−",
              "+": "+", "esc": "Esc"}


def keyboard_keys(label: str) -> str | None:
    """«1–5» → «1,2,3,4,5»; «← → ↑ ↓» → стрелки; «+ −» → обе. Ключи — help_drawings.KeyboardDrawing."""
    label = label.strip()
    if "–" in label and label.replace("–", "").isdigit():
        a, b = (int(x) for x in label.split("–"))
        return ",".join(str(i) for i in range(a, b + 1))
    keys = [_KEY_NAMES.get(part.lower(), part.upper() if len(part) <= 3 else part) for part in label.split()]
    return ",".join(keys) or None


class _Row(QFrame):
    """Строка списка: клавиша-подсказка и действие; наведение — подсветка на рисунке."""

    def __init__(self, dialog: HotkeysDialog, index: int, key: str, what: str):
        super().__init__()
        self._dialog, self.index = dialog, index
        self.setObjectName("helpRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP[1] + 2, SP[1] - 2, SP[1], SP[1] - 2)
        lay.setSpacing(SP[2])
        self.key_label = QLabel(key)
        self.key_label.setMinimumWidth(64)
        self.key_label.setAlignment(Qt.AlignCenter)
        self.what_label = QLabel(what)
        self.what_label.setWordWrap(True)
        lay.addWidget(self.key_label, 0, Qt.AlignTop)
        lay.addWidget(self.what_label, 1)
        self.set_lit(False)

    def set_lit(self, on: bool) -> None:
        self.lit = on
        self.setStyleSheet(
            f"QFrame#helpRow {{ background: {_rgba(C.LIME, 0.10) if on else 'transparent'};"
            f" border-radius: {R_BUTTON}px; }}")
        self.key_label.setStyleSheet(
            f"color: {C.LIME if on else C.TEXT}; background: {C.RAISED}; border: 1px solid"
            f" {C.LIME if on else C.LINE}; border-radius: {R_TAG}px; padding: 2px 6px;"
            f" font-size: {FS_CAPTION - 1}px; font-weight: 600;")
        self.what_label.setStyleSheet(f"color: {C.TEXT if on else C.TEXT_DIM};")

    def enterEvent(self, _e):  # noqa: N802
        self._dialog.row_hovered(self.index)

    def leaveEvent(self, _e):  # noqa: N802
        self._dialog.row_hovered(None)


class HotkeysDialog(QDialog):
    """Живая справка: вкладки Геймпад / Клавиатура / Мышь, рисунок слева, список справа.
    Наведение на строку подсвечивает кнопку на рисунке (стик ещё и двигается), наведение на рисунок —
    строку и прокручивает список к ней. Пояснение — только в списке справа: рисунок никогда не сдвигается."""

    TABS = ("Геймпад", "Клавиатура", "Мышь")

    def __init__(self, keyboard: list[tuple[str, str]] | None = None, parent=None):
        super().__init__(parent)
        from ptztracker.ui.main_window import _HOTKEYS  # поздний импорт: main_window импортирует этот модуль

        self.setWindowTitle("Справка")
        self.keys_rows = [*(keyboard if keyboard is not None else _HOTKEYS), ("F1", "эта справка")]
        self.pad_rows = list(GAMEPAD_HELP)
        self.mouse_rows = list(MOUSE_HELP)
        self._data = (
            [(k, v, pad_elements(k)) for k, v in self.pad_rows],
            [(k, v, keyboard_keys(k)) for k, v in self.keys_rows],
            [(k, v, e) for (k, v), e in zip(self.mouse_rows, ("m_target", "m_zone", "m_cell"), strict=True)],
        )
        self.pad = PadDrawing()
        self.keyboard = KeyboardDrawing({x for _k, _v, keys in self._data[1] if keys for x in keys.split(",")})
        self.mouse = MouseDrawing()
        self.drawings = (self.pad, self.keyboard, self.mouse)
        for i, d in enumerate(self.drawings):
            d.hovered.connect(lambda key, i=i: self.drawing_hovered(i, key))

        head = QHBoxLayout()
        head.setSpacing(SP[2])
        head.addWidget(BrandMark(22))
        title = QLabel("Справка")
        title.setStyleSheet(f"font-size: {FS_BUTTON}px; font-weight: 700;")
        head.addWidget(title)
        self.tabs = QTabBar()
        for t in self.TABS:
            self.tabs.addTab(t)
        self.tabs.setDrawBase(False)
        self.tabs.setStyleSheet(
            f"QTabBar::tab {{ padding: 6px 14px; margin-right: 4px; border-radius: 7px; color: {C.TEXT_DIM};"
            f" background: transparent; }}"
            f"QTabBar::tab:selected {{ background: {C.RAISED_HI}; color: {C.TEXT}; }}")
        head.addSpacing(SP[3])
        head.addWidget(self.tabs)
        head.addStretch(1)

        self.stack = QStackedWidget()
        for d in self.drawings:
            self.stack.addWidget(d)
        # одна постоянная строка-подсказка вместо меняющегося пояснения: рисунок не прыгает, текст не дублируется
        self.tip_label = QLabel("Наведите на кнопку или на строку справа — подсветится пара")
        self.tip_label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        left = QVBoxLayout()
        left.addWidget(self.stack, 1)
        left.addWidget(self.tip_label)

        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(SP[1], SP[1], SP[1], SP[1])
        self.list_lay.setSpacing(2)
        scroll = self.list_scroll = QScrollArea()
        scroll.setWidget(self.list_host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(380)
        line = QFrame()
        line.setFixedWidth(1)
        line.setStyleSheet(f"background: {C.LINE};")
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addLayout(left, 1)
        body.addWidget(line)
        body.addWidget(scroll)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[3], SP[2], SP[3], SP[3])
        lay.setSpacing(SP[2])
        lay.addLayout(head)
        lay.addLayout(body, 1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(close)
        lay.addLayout(foot)
        self.rows: list[_Row] = []
        self.tabs.currentChanged.connect(self._show_tab)
        self._show_tab(0)
        self.resize(1040, 560)

    def _show_tab(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        for r in self.rows:
            r.setParent(None)
        while self.list_lay.count():
            self.list_lay.takeAt(0)
        self.rows = [_Row(self, n, k, v) for n, (k, v, _e) in enumerate(self._data[i])]
        key_w = min(170, max(r.key_label.sizeHint().width() for r in self.rows))
        for r in self.rows:  # колонка клавиш одной ширины — действия начинаются ровно
            r.key_label.setFixedWidth(key_w)
            r.key_label.setWordWrap(True)
            self.list_lay.addWidget(r)
        self.list_lay.addStretch(1)
        for d in self.drawings:
            d.set_highlight(None)

    @property
    def drawing(self):
        return self.drawings[self.tabs.currentIndex()]

    def highlight(self, row: int | None) -> None:
        data = self._data[self.tabs.currentIndex()]
        for r in self.rows:
            r.set_lit(r.index == row)
        self.drawing.set_highlight(data[row][2] if row is not None else None)

    def row_hovered(self, row: int | None) -> None:
        self.highlight(row)

    def drawing_hovered(self, tab: int, key: str | None) -> None:
        if tab != self.tabs.currentIndex():
            return
        data = self._data[tab]
        match = None
        if key is not None:
            found = [n for n, (_k, _v, keys) in enumerate(data)
                     if keys and key in (self.pad.elements_for(keys) if tab == 0 else set(keys.split(",")))]
            # у одного элемента бывает два действия (тачпад: нажать — стоп, свайп — крупность): стоп важнее
            match = next((n for n in found if "СТОП" in data[n][1]), found[0] if found else None)
        self.highlight(match)
        if match is not None:  # навели на рисунок — строка справа сама прокручивается в видимую часть
            self.list_scroll.ensureWidgetVisible(self.rows[match], 0, SP[2])
        if match is None and key is not None:
            self.drawing.set_highlight(None)
