from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


class C:
    """Токены цвета (единственный источник; QSS и виджеты берут отсюда)."""

    BG = "#13161C"  # фон окна
    SURFACE = "#1B1F27"  # панели, меню, статус
    RAISED = "#252A34"  # кнопки, поля
    RAISED_HI = "#2E3440"  # наведение, дорожка ползунка
    LINE = "#333A47"  # границы
    TEXT = "#E7EAF0"
    TEXT_DIM = "#A0A8B6"
    TEXT_FAINT = "#6C7486"
    ACCENT = "#4C8DF6"  # выбор, действие
    ON = "#3D8BFF"  # слежение ведёт
    LOST = "#F0A020"  # цель потеряна
    REMOTE = "#A78BFA"  # пульт
    FAULT = "#EF4444"  # раскачка, стоп
    PGM = "#E8202A"  # Tally: эфир
    PVW = "#1FB35A"  # Tally: превью


FONT_FAMILY = "Segoe UI"
FS_CAPTION, FS_TEXT, FS_BUTTON, FS_STATE = 12, 14, 15, 24  # px
H_BUTTON, H_CHOICE, H_STOP, H_STATE = 34, 40, 44, 118  # px
SP = (4, 8, 12, 16)
R_BUTTON, R_PANEL = 6, 10


def qcolor(hex: str, alpha: int = 255) -> QColor:
    c = QColor(hex)
    c.setAlpha(alpha)
    return c


def _rgba(hex: str, alpha: float) -> str:
    c = QColor(hex)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


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
QPushButton:checked {{ background: {_rgba(C.ACCENT, 0.24)}; border-color: {C.ACCENT}; }}
QPushButton:disabled {{ color: {C.TEXT_FAINT}; background: {C.SURFACE}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {C.RAISED_HI}; }}
QPushButton[variant="choice"] {{ min-height: {H_CHOICE}px; }}
QPushButton[variant="choice"]:checked {{ background: {_rgba(C.ACCENT, 0.24)};
    border-color: {C.ACCENT}; }}
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
    padding: 0 {SP[1]}px; selection-background-color: {C.ACCENT}; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ background: {C.RAISED_HI}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {C.ACCENT}; }}
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled {{ color: {C.TEXT_FAINT}; }}
QComboBox QAbstractItemView {{ background: {C.RAISED}; border: 1px solid {C.LINE};
    selection-background-color: {C.ACCENT}; outline: 0; }}

QCheckBox {{ spacing: {SP[1]}px; }}
QCheckBox:disabled {{ color: {C.TEXT_FAINT}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {C.LINE};
    border-radius: 4px; background: {C.RAISED}; }}
QCheckBox::indicator:checked {{ background: {C.ACCENT}; border-color: {C.ACCENT}; }}

QSlider::groove:horizontal {{ height: 6px; background: {C.RAISED_HI}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {C.ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 18px; height: 18px; margin: -6px 0;
    border-radius: 9px; background: {C.TEXT}; }}
QSlider::handle:horizontal:disabled {{ background: {C.TEXT_FAINT}; }}

QScrollBar:vertical {{ width: 8px; background: transparent; margin: 0; }}
QScrollBar::handle:vertical {{ background: {C.RAISED_HI}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {C.LINE}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QMenuBar {{ background: {C.SURFACE}; }}
QMenuBar::item {{ background: transparent; padding: 4px {SP[2]}px; }}
QMenuBar::item:selected {{ background: {C.RAISED_HI}; }}
QMenu {{ background: {C.SURFACE}; border: 1px solid {C.LINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {C.RAISED_HI}; }}
QMenu::item:disabled {{ color: {C.TEXT_FAINT}; }}
QMenu::separator {{ height: 1px; background: {C.LINE}; margin: 4px 8px; }}
QStatusBar {{ background: {C.SURFACE}; color: {C.TEXT_DIM}; }}
QStatusBar::item {{ border: none; }}

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
    p.setColor(QPalette.Highlight, q(C.ACCENT))
    p.setColor(QPalette.HighlightedText, q("#FFFFFF"))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, q(C.TEXT_FAINT))
    app.setPalette(p)
    font = QFont(FONT_FAMILY)
    font.setPixelSize(FS_TEXT)
    app.setFont(font)
    app.setStyleSheet(stylesheet())
