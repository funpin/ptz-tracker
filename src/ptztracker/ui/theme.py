from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


class C:
    """Токены цвета (единственный источник; QSS и виджеты берут отсюда). Смысл — docs/BRANDBOOK.md §3."""

    BG = "#17191D"  # Graphite: фон окна
    SURFACE = "#1E2126"  # панели, шапки клеток, меню, статус
    RAISED = "#272B31"  # кнопки, поля
    RAISED_HI = "#30353C"  # наведение, дорожка ползунка
    LINE = "#33373E"  # все линии и рамки
    TEXT = "#F5F5F2"  # Soft White: текст, знак, выбранное
    TEXT_DIM = "#B4B8BD"
    TEXT_FAINT = "#858A91"
    GREY = "#777C83"  # Neutral Grey: контуры, отметки (для мелкого текста тёмный)
    LIME = "#B7F34A"  # Tracking Lime: только активное
    ACCENT = LIME  # действие, включённое
    SELECT = TEXT  # рамка выбранной клетки
    ON = LIME  # слежение ведёт
    LOST = "#F0A020"  # цель потеряна, мало заряда
    REMOTE = TEXT  # пульт
    FAULT = "#EF4444"  # раскачка, стоп
    PGM = "#E8202A"  # Tally: эфир (сплошной)
    PVW = "#2FB65E"  # Tally: превью (только контуром — не путать с лаймом)
    OK = TEXT  # значки верхней панели: «всё в порядке»


FONT_FAMILY = "Inter Variable"  # встроен (ui/fonts, лицензия OFL): одинаково на Windows и Mac
_FONTS_DIR = Path(__file__).resolve().parent / "fonts"
_fonts_loaded = False


def load_fonts() -> str:
    """Зарегистрировать Inter из папки программы (один раз); вернуть имя семейства."""
    global _fonts_loaded
    if not _fonts_loaded:
        for f in sorted(_FONTS_DIR.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(f))
        _fonts_loaded = True
    return FONT_FAMILY
FS_CAPTION, FS_TEXT, FS_BUTTON, FS_STATE = 12, 13, 15, 24  # px
H_BUTTON, H_CHOICE, H_STOP, H_STATE = 34, 40, 42, 118  # px
SP = (4, 8, 12, 16, 24)
R_BUTTON, R_PANEL, R_TAG = 8, 10, 5


def qcolor(hex: str, alpha: int = 255) -> QColor:
    c = QColor(hex)
    c.setAlpha(alpha)
    return c


def _rgba(hex: str, alpha: float) -> str:
    c = QColor(hex)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


# галочка выпадающего списка: свой рисунок вместо системного отсека (на Windows он выбивался из стиля)
CHEVRON = (Path(__file__).resolve().parent / "icons" / "chevron-down.svg").as_posix()
CHEVRON_UP = (Path(__file__).resolve().parent / "icons" / "chevron-up.svg").as_posix()


def stylesheet() -> str:
    """Общий QSS. Цвета только из C."""
    return f"""
QMainWindow, QWidget {{ background: {C.BG}; color: {C.TEXT};
    font-family: "{FONT_FAMILY}"; font-size: {FS_TEXT}px; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {C.RAISED}; color: {C.TEXT}; border: 1px solid {C.LINE};
    padding: 4px 8px; }}

QPushButton {{ background: {C.RAISED}; color: {C.TEXT}; border: 1px solid {C.LINE};
    border-radius: {R_BUTTON}px; min-height: {H_BUTTON}px; padding: 0 {SP[2]}px;
    font-size: {FS_BUTTON}px; }}
QPushButton:hover {{ background: {C.RAISED_HI}; }}
QPushButton:checked {{ background: {_rgba(C.TEXT, 0.06)}; border-color: {C.TEXT}; }}
QPushButton:disabled {{ color: {C.TEXT_FAINT}; background: {C.SURFACE}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {C.RAISED_HI}; }}
QPushButton[variant="choice"] {{ min-height: {H_CHOICE}px; }}
QPushButton[variant="choice"]:checked {{ background: {_rgba(C.TEXT, 0.06)};
    border-color: {C.TEXT}; }}
QPushButton[variant="primary"] {{ background: {C.TEXT}; color: {C.BG}; border-color: {C.TEXT};
    font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {C.TEXT_DIM}; border-color: {C.TEXT_DIM}; }}
QPushButton[variant="primary"]:disabled {{ background: {C.RAISED}; color: {C.TEXT_FAINT}; border-color: {C.LINE}; }}
QPushButton[variant="seg"] {{ min-height: 28px; padding: 0 {SP[2]}px; font-size: {FS_TEXT}px; }}
QPushButton[variant="seg"]:checked {{ background: {C.TEXT}; color: {C.BG}; border-color: {C.TEXT};
    font-weight: 600; }}
QPushButton[variant="seg"]:checked:disabled {{ background: {C.TEXT_DIM}; color: {C.BG}; border-color: {C.TEXT_DIM}; }}
QPushButton[variant="stop"] {{ min-height: {H_STOP}px; color: {C.FAULT};
    border: 1px solid {C.FAULT}; font-weight: 700; }}
QPushButton[variant="stop"]:hover {{ background: {_rgba(C.FAULT, 0.18)}; }}
QPushButton[variant="stop"]:disabled {{ color: {C.TEXT_FAINT}; border-color: {C.LINE}; }}
QLabel[variant="section"] {{ color: {C.TEXT_DIM}; font-size: 12.5px; font-weight: 600; }}

QLabel[kind] {{ border: 1px solid {C.LINE}; border-radius: {R_BUTTON}px;
    padding: 2px {SP[1]}px; font-size: {FS_CAPTION}px; color: {C.TEXT_DIM}; }}
QLabel[kind="neutral"] {{ color: {C.TEXT_DIM}; border-color: {C.LINE}; }}
QLabel[kind="ok"] {{ color: {C.PVW}; border-color: {_rgba(C.PVW, 0.5)}; }}
QLabel[kind="accent"] {{ color: {C.ACCENT}; border-color: {_rgba(C.ACCENT, 0.5)}; }}
QLabel[kind="warn"] {{ color: {C.LOST}; border-color: {_rgba(C.LOST, 0.5)}; }}
QLabel[kind="fault"] {{ color: {C.FAULT}; border-color: {_rgba(C.FAULT, 0.5)}; }}
QLabel[kind="pgm"] {{ background: {C.PGM}; color: {C.TEXT}; border-color: {C.PGM};
    font-weight: 700; }}
QLabel[kind="pvw"] {{ color: {C.PVW}; border-color: {_rgba(C.PVW, 0.5)}; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{ background: {C.RAISED};
    border: 1px solid {C.LINE}; border-radius: {R_BUTTON}px; min-height: {H_BUTTON - 2}px;
    padding: 0 {SP[1]}px; selection-background-color: {C.RAISED_HI}; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ background: {C.RAISED_HI}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {C.TEXT_DIM}; }}
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled {{ color: {C.TEXT_FAINT}; }}
QComboBox {{ padding-right: {SP[3] + SP[1]}px; }}
QComboBox::drop-down {{ border: none; width: {SP[3]}px; subcontrol-origin: padding;
    subcontrol-position: right center; }}
QComboBox::down-arrow {{ image: url({CHEVRON}); width: 10px; height: 10px; }}
QComboBox::down-arrow:disabled {{ image: none; }}
QSpinBox, QDoubleSpinBox {{ padding-right: {SP[3]}px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: {SP[3]}px; border: none; background: transparent; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({CHEVRON_UP}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({CHEVRON}); width: 10px; height: 10px; }}
QSpinBox::up-arrow:disabled, QSpinBox::down-arrow:disabled {{ image: none; }}
QComboBox QAbstractItemView {{ background: {C.RAISED}; border: 1px solid {C.LINE};
    selection-background-color: {C.RAISED_HI}; outline: 0; }}

QCheckBox {{ spacing: {SP[1]}px; }}
QCheckBox:disabled {{ color: {C.TEXT_FAINT}; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {C.GREY};
    border-radius: 4px; background: transparent; }}
QCheckBox::indicator:checked {{ background: {C.LIME}; border-color: {C.LIME}; }}

QSlider:horizontal {{ min-height: 20px; }}  /* ручка 17 px — ползунок ниже срезал бы её (Mac) */
QSlider:vertical {{ min-width: 20px; }}
QSlider::groove:horizontal {{ height: 3px; background: {C.RAISED_HI}; border-radius: 1px; }}
QSlider::sub-page:horizontal {{ background: {C.LIME}; border-radius: 1px; }}
QSlider::sub-page:horizontal:disabled {{ background: {C.GREY}; }}
QSlider::handle:horizontal {{ width: 18px; height: 16px; margin: -7px 0;
    border-radius: 6px; background: {C.TEXT_FAINT}; }}
QSlider::handle:horizontal:hover {{ background: {C.TEXT_DIM}; }}
QSlider::handle:horizontal:disabled {{ background: {C.RAISED_HI}; border: 1px solid {C.GREY}; width: 16px; height: 14px; }}
QSlider::groove:vertical {{ width: 4px; background: {C.RAISED_HI}; border-radius: 2px; }}
QSlider::add-page:vertical {{ background: {C.LIME}; border-radius: 2px; }}
QSlider::add-page:vertical:disabled {{ background: {C.GREY}; }}
QSlider::handle:vertical {{ width: 18px; height: 16px; margin: 0 -7px;
    border-radius: 6px; background: {C.TEXT_FAINT}; }}
QSlider::handle:vertical:hover {{ background: {C.TEXT_DIM}; }}
QSlider::handle:vertical:disabled {{ background: {C.RAISED_HI}; border: 1px solid {C.GREY}; width: 16px; height: 14px; }}

QScrollBar:vertical {{ width: 8px; background: transparent; margin: 0; }}
QScrollBar::handle:vertical {{ background: {C.RAISED_HI}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {C.LINE}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ height: 6px; background: transparent; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {C.RAISED_HI}; border-radius: 3px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {C.LINE}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

QMenuBar {{ background: {C.SURFACE}; }}
QMenuBar::item {{ background: transparent; padding: 4px {SP[2]}px; }}
QMenuBar::item:selected {{ background: {C.RAISED_HI}; }}
QMenu {{ background: {C.SURFACE}; border: 1px solid {C.LINE}; border-radius: {R_PANEL}px; padding: 6px; }}
QMenu::item {{ padding: 7px 20px 7px 10px; border-radius: 7px; }}
QMenu::item:selected {{ background: {C.RAISED_HI}; }}
QMenu::item:disabled {{ color: {C.TEXT_FAINT}; }}
QMenu::separator {{ height: 1px; background: {C.LINE}; margin: 4px 8px; }}
QStatusBar {{ background: {C.SURFACE}; color: {C.TEXT_DIM}; border-top: 1px solid {C.LINE};
    padding: 2px {SP[1]}px; }}
QStatusBar::item {{ border: none; margin: 0 {SP[0]}px; }}
QStatusBar QWidget {{ background: transparent; }}
QStatusBar QToolButton:hover {{ background: {C.RAISED_HI}; border-radius: 4px; }}
QToolButton[pill="true"]::menu-indicator {{ image: none; width: 0; }}

QGroupBox {{ border: none; margin-top: {SP[3]}px; padding-top: {SP[1]}px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 0; color: {C.TEXT_DIM};
    font-size: 12.5px; font-weight: 600; }}
"""


def apply_dark_theme(app: QApplication) -> None:
    """Тёмная тема: за пультом обычно темно, яркое окно мешает."""
    app.setStyle("Fusion")
    p = QPalette()
    q = qcolor
    p.setColor(QPalette.Window, q(C.BG))
    p.setColor(QPalette.WindowText, q(C.TEXT))
    p.setColor(QPalette.Base, q(C.SURFACE))
    p.setColor(QPalette.AlternateBase, q(C.RAISED))
    p.setColor(QPalette.Text, q(C.TEXT))
    p.setColor(QPalette.Button, q(C.RAISED))
    p.setColor(QPalette.ButtonText, q(C.TEXT))
    p.setColor(QPalette.ToolTipBase, q(C.RAISED))
    p.setColor(QPalette.ToolTipText, q(C.TEXT))
    p.setColor(QPalette.Highlight, q(C.RAISED_HI))
    p.setColor(QPalette.HighlightedText, q(C.TEXT))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, q(C.TEXT_FAINT))
    app.setPalette(p)
    load_fonts()
    font = QFont(FONT_FAMILY)
    font.setPixelSize(FS_TEXT)
    app.setFont(font)
    app.setStyleSheet(stylesheet())


def install_russian(app: QApplication) -> None:
    """Стандартные кнопки и окна Qt (Cancel, Yes/No, выбор папки) — по-русски."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    if getattr(app, "_ru_translator", None) is not None:
        return
    tr = QTranslator(app)
    if tr.load(QLocale(QLocale.Russian), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)
        app._ru_translator = tr  # держим ссылку: иначе переводчик соберёт сборщик мусора


# цвет и номер клетки на виде сверху и в графиках служебных клеток
SLOT_COLORS = (C.TEXT, C.LIME, C.LOST, C.TEXT_DIM)
