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

from ptztracker.config import SPEED_GEARS
from ptztracker.control.manual_curve import PAD_GEAR_DEG_S
from ptztracker.ui.help_drawings import KeyboardDrawing, MouseDrawing, PadDrawing, PadGlyphs
from ptztracker.ui.theme import FS_BUTTON, FS_CAPTION, R_BUTTON, R_TAG, SP, C, _rgba
from ptztracker.ui.widgets import BrandMark

# раскладка — спека 2026-10-30-gamepad-dualsense-design.md; меняя её, правьте gamepad/mapping.py и спеку
GAMEPAD_HELP: list[tuple[str, str]] = [
    ("Левый стик · L3", "поворот и наклон, скорость — по отклонению; нажать — разовая автофокусировка"),
    ("Правый стик · R3", "← / → — цель: сосед слева / справа; нажать — слежение вкл / выкл"),
    ("L2 / R2", "зум шире / ближе"),
    ("L1 / R1", f"стик медленнее / быстрее: {SPEED_GEARS} ступеней, {PAD_GEAR_DEG_S[0]}–{PAD_GEAR_DEG_S[-1]} °/с"
                " (или чувствительность — ☰ → «Геймпады…»)"),
    ("✕  ○  □  △", "пресеты 1–4: нажать — вызвать, держать 1 с — запомнить"),
    ("Крестовина ← / →", "ирис закрыть / открыть (на «Авто» — снимает авто и сразу делает шаг)"),
    ("Крестовина ↑ / ↓", "gain выше / ниже на 3 dB (на «Авто» — первое нажатие снимает авто: последнее ручное значение или 0 dB)"),
    ("Тачпад (нажать)", "СТОП КАМЕРЫ"),
    ("Свайп по тачпаду → / ←", "крупность: вправо — шире (пояс → рост → общий), влево — ближе; быстро, пальцем на треть тачпада"),
    ("Options", "быстрые настройки — меню поверх видео: фокус, баланс, ирис, gain, сцена, включить / выключить камеру"
                " (крестовина — пункт и значение, ✕ принять, ○ или △ закрыть)"),
    ("Create", "нажать — следующая камера (у главного геймпада в виде «1 камера» — и в программе);"
               " держать — запись видео этой камеры: включить или остановить (как R)"),
    ("PS", "эта справка — открыть / закрыть"),
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
                        ("крестовина ↑", "dpad_ud"), ("крестовина", "dpad_lr"), ("тачпад", "touchpad"), ("свайп", "swipe"),
                        ("options", "options"), ("create", "create"), ("ps", "ps")):
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
    """Строка списка: клавиша-подсказка (у геймпада — кнопки той же формы, что на рисунке) и действие;
    наведение — подсветка на рисунке."""

    def __init__(self, dialog: HotkeysDialog, index: int, key: str, what: str, glyphs: list[str] | None = None):
        super().__init__()
        self._dialog, self.index, self.key = dialog, index, key
        self.setObjectName("helpRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP[1] + 2, SP[1] - 2, SP[1], SP[1] - 2)
        lay.setSpacing(SP[2])
        self.key_label = QLabel(key)
        self.key_label.setMinimumWidth(64)
        self.key_label.setAlignment(Qt.AlignCenter)
        self.glyphs = PadGlyphs(glyphs) if glyphs else None
        self.what_label = QLabel(what)
        self.what_label.setWordWrap(True)
        self.glyph_host = None
        if self.glyphs is not None:  # кнопки — по центру колонки одной ширины
            self.glyphs.setToolTip(key)
            self.glyph_host = QWidget()
            self.glyph_host.setStyleSheet("background: transparent;")  # подсветка строки видна и под кнопками
            gh = QHBoxLayout(self.glyph_host)
            gh.setContentsMargins(0, 0, 0, 0)
            gh.addWidget(self.glyphs, 0, Qt.AlignCenter)
            lay.addWidget(self.glyph_host, 0, Qt.AlignVCenter)
        else:
            lay.addWidget(self.key_label, 0, Qt.AlignTop)
        lay.addWidget(self.what_label, 1)
        self.set_lit(False)

    def set_lit(self, on: bool, only: set[str] | None = None) -> None:
        """only — какие кнопки строки горят (навели на одну на рисунке); None — все."""
        self.lit = on
        if self.glyphs is not None:
            self.glyphs.set_lit(set() if not on else set(self.glyphs.keys) if only is None else only)
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
        self.rows = [_Row(self, n, k, v, self._glyph_keys(e) if i == 0 else None)
                     for n, (k, v, e) in enumerate(self._data[i])]
        key_w = min(170, max(r.key_label.sizeHint().width() for r in self.rows))
        glyph_w = max((r.glyphs.width() for r in self.rows if r.glyphs), default=0)
        for r in self.rows:  # колонка клавиш одной ширины — действия начинаются ровно
            if r.glyph_host is not None:
                r.glyph_host.setFixedWidth(glyph_w)
            r.key_label.setFixedWidth(key_w)
            r.key_label.setWordWrap(True)
            self.list_lay.addWidget(r)
        self.list_lay.addStretch(1)
        for d in self.drawings:
            d.set_highlight(None)

    @property
    def drawing(self):
        return self.drawings[self.tabs.currentIndex()]

    @staticmethod
    def _glyph_keys(keys: str | None) -> list[str]:
        """Ключи рисунка строки → кнопки в списке: «l1,r1» → [l1, r1] (бамперы рисуются по порядку L, R)."""
        out = [k for k in (keys or "").split(",") if k]
        return sorted(out) if out and all(k[:1] in "lr" and k[1:] in "12" for k in out) else out

    def highlight(self, row: int | None, only: str | None = None) -> None:
        """only — элемент рисунка, на который навели: горит только он (и в строке, и на рисунке)."""
        data = self._data[self.tabs.currentIndex()]
        lit_glyphs = None
        if only is not None and row is not None and self.tabs.currentIndex() == 0:
            lit_glyphs = {g for g in self._glyph_keys(data[row][2]) if only in self.pad.elements_for(g)}
        for r in self.rows:
            r.set_lit(r.index == row, lit_glyphs if r.index == row else None)
        if row is None:
            self.drawing.set_highlight(None)
        elif lit_glyphs and len(lit_glyphs) < len(self._glyph_keys(data[row][2])):
            self.drawing.set_highlight(",".join(sorted(lit_glyphs)))
        else:
            self.drawing.set_highlight(data[row][2])

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
        self.highlight(match, key if tab == 0 else None)
        if match is not None:  # навели на рисунок — строка справа сама прокручивается в видимую часть
            self.list_scroll.ensureWidgetVisible(self.rows[match], 0, SP[2])
        if match is None and key is not None:
            self.drawing.set_highlight(None)
