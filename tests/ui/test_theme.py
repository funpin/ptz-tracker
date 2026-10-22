import re

from PySide6.QtWidgets import QApplication

from ptztracker.ui.theme import C, apply_dark_theme, stylesheet


def test_tokens_match_mockup():
    assert C.ON == "#3D8BFF"
    assert C.PGM == "#E8202A"
    assert C.PVW == "#1FB35A"
    assert C.FAULT == "#EF4444"


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
