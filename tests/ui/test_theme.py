import re

from PySide6.QtWidgets import QApplication

from ptztracker.ui.theme import C, apply_dark_theme, stylesheet

BRANDBOOK = {
    "BG": "#17191D", "SURFACE": "#1E2126", "RAISED": "#272B31", "RAISED_HI": "#30353C", "LINE": "#33373E",
    "TEXT": "#F5F5F2", "TEXT_DIM": "#B4B8BD", "TEXT_FAINT": "#858A91", "GREY": "#777C83", "LIME": "#B7F34A",
    "LOST": "#F0A020", "FAULT": "#EF4444", "PGM": "#E8202A", "PVW": "#2FB65E",
}


def test_tokens_match_brandbook():
    for name, value in BRANDBOOK.items():
        assert getattr(C, name) == value, name
    assert C.ON == C.LIME and C.ACCENT == C.LIME
    assert C.SELECT == C.TEXT


def test_no_blue_tokens():
    for k, v in vars(C).items():
        if k.isupper():
            r, g, b = (int(v[i:i + 2], 16) for i in (1, 3, 5))
            assert b <= max(r, g) + 20, k  # синий канал не преобладает


def test_checked_controls_use_lime():
    qss = stylesheet()

    def rule(selector: str) -> str:
        return qss.split(selector, 1)[1].split("}", 1)[0]

    assert C.LIME in rule("QCheckBox::indicator:checked")
    assert C.LIME in rule("QSlider::sub-page:horizontal")
    assert C.TEXT in rule("QPushButton:checked")
    assert f"selection-background-color: {C.RAISED_HI}" in rule("QComboBox QAbstractItemView")


def test_stylesheet_uses_only_tokens():
    tokens = {v.lower() for k, v in vars(C).items() if k.isupper()}
    used = set(re.findall(r"#[0-9a-fA-F]{6}\b", stylesheet()))
    assert used
    assert {u.lower() for u in used} <= tokens


def test_apply_dark_theme_sets_palette(qapp: QApplication):
    app = QApplication.instance()
    try:
        apply_dark_theme(app)
        assert app.palette().window().color().name() == C.BG.lower()
        assert app.styleSheet().strip()
    finally:
        app.setStyleSheet("")


def test_icon_files_exist_and_square():
    from pathlib import Path

    from PySide6.QtGui import QImage

    icons = Path(__file__).resolve().parents[2] / "packaging" / "icons"
    for name in ("ptztracker.ico", "ptztracker.icns", "ptztracker-1024.png"):
        assert (icons / name).stat().st_size > 1000, name
    img = QImage(str(icons / "ptztracker-1024.png"))
    assert img.width() == img.height() == 1024


def test_inter_is_loaded(qapp):
    from PySide6.QtGui import QFontDatabase

    from ptztracker.ui.theme import FONT_FAMILY, load_fonts

    assert load_fonts() == FONT_FAMILY == "Inter Variable"
    assert load_fonts() == FONT_FAMILY  # повторный вызов не дублирует
    assert FONT_FAMILY in QFontDatabase.families()
    app = QApplication.instance()
    try:
        apply_dark_theme(app)
        assert app.font().family() == FONT_FAMILY
    finally:
        app.setStyleSheet("")
