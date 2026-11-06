"""Горячие клавиши не зависят от раскладки: русская «Ц» — это W, «№» с Shift — это 3.

Qt отдаёт в key() символ текущей раскладки, поэтому без этого WASD, Z X C и M молчат при русском языке,
а Shift + цифра приходит как «!», «@», «№»…"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt

# ЙЦУКЕН → QWERTY: одна и та же физическая клавиша
_CYRILLIC = dict(zip(
    (ord(c) for c in "ЙЦУКЕНГШЩЗФЫВАПРОЛДЯЧСМИТЬ"),
    (getattr(Qt, f"Key_{c}") for c in "QWERTYUIOPASDFGHJKLZXCVBNM"),
    strict=True,
))
# Shift + цифра: английская и русская раскладки
_SHIFTED = {
    Qt.Key_Exclam: 1, Qt.Key_At: 2, Qt.Key_NumberSign: 3, Qt.Key_Dollar: 4, Qt.Key_Percent: 5,
    Qt.Key_AsciiCircum: 6, Qt.Key_Ampersand: 7, Qt.Key_Asterisk: 8, Qt.Key_ParenLeft: 9,
    Qt.Key_QuoteDbl: 2, 0x2116: 3, Qt.Key_Semicolon: 4, Qt.Key_Colon: 6, Qt.Key_Question: 7,
}
# виртуальные коды цифр 1–9 — на случай других раскладок; у Windows и macOS коды разные
# (на Windows 0x12 — Alt, поэтому таблица своя для каждой системы)
_NATIVE = ({0x12: 1, 0x13: 2, 0x14: 3, 0x15: 4, 0x17: 5, 0x16: 6, 0x1A: 7, 0x1C: 8, 0x19: 9}
           if sys.platform == "darwin" else {0x31 + i: i + 1 for i in range(9)} if sys.platform == "win32" else {})


def logical_key(key: int) -> int:
    return _CYRILLIC.get(key, key)


def digit_of(key: int, native_vk: int) -> int | None:
    """Цифра 1–9 клавиши — с Shift или без, в любой раскладке; иначе None."""
    if Qt.Key_1 <= key <= Qt.Key_9:
        return key - Qt.Key_0
    if key in _SHIFTED:
        return _SHIFTED[key]
    return _NATIVE.get(native_vk) if native_vk else None
