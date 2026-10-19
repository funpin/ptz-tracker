from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_dark_theme(app: QApplication) -> None:
    """Тёмная тема: за пультом обычно темно, яркое окно мешает."""
    app.setStyle("Fusion")
    p = QPalette()
    base, alt, text = QColor(30, 33, 40), QColor(40, 44, 54), QColor(225, 228, 235)
    p.setColor(QPalette.Window, base)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, QColor(22, 24, 30))
    p.setColor(QPalette.AlternateBase, alt)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, alt)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.ToolTipBase, alt)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Highlight, QColor(59, 130, 246))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 115, 125))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 115, 125))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(110, 115, 125))
    app.setPalette(p)
