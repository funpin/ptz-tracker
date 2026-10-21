import re
from pathlib import Path

import ptztracker.ui as ui_pkg

HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
QCOL = re.compile(r"QColor\(\s*\d")


def test_only_theme_has_colors():
    bad = []
    for p in Path(ui_pkg.__file__).parent.glob("*.py"):
        if p.name == "theme.py":
            continue
        text = p.read_text(encoding="utf-8")
        if HEX.search(text) or QCOL.search(text):
            bad.append(p.name)
    assert not bad, f"ручные цвета вне theme.py: {bad}"
