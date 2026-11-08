"""Общее для частей главного окна: настройки, таблицы клавиш, мелкие помощники."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QFrame

from ptztracker.gamepad.intents import Disconnected, PanTilt, Zoom
from ptztracker.ui.theme import C

SPEED_SCALE_SAVE_S = 60.0  # поправку скорости от слежения пишем в настройки не чаще раза в минуту

SPEED_SCALE_SAVE_DIFF = 0.01  # меньшее изменение поправки не сохраняем

KEY_PAN_SPEED = 0.4  # до кривой чувствительности: «Резко» — как в 1.0.1

ZOOM_POLL_S = 1.0  # «медленнее на крупном плане»: #GZ не чаще раза в секунду на камеру (слежение спрашивает само)

KEY_ZOOM_SPEED = 0.5

SIDE_WIDTH = 372

_HOTKEYS = [
    ("Пробел", "слежение"), ("1–9", "пресет выбранной камеры; Shift + цифра — записать (спросит)"),
    ("W A S D", "поворот, пока держите (как стрелки); с Shift — быстрее"), ("← → ↑ ↓", "поворот; с Shift — быстрее"),
    ("+ −", "зум"), ("Z X C", "кадр: пояс, рост, общий"), ("Esc", "стоп"),
    ("R", "запись видео выбранной камеры: начать / остановить"),
    ("F9", "сохранить момент"), ("M", "быстрые настройки (меню поверх видео)"), ("P", "панель пресетов"),
    ("G", "4 камеры ↔ 1 камера крупно"),
    ("Tab", "управление мышью: ведёте — едет, колесо — зум (ПКМ — ирис), ЛКМ — взять человека; Tab — выйти"),
]  # F1 («справка») сюда не добавляем: окно F1 показывает её само

# в строке состояния — только главное; остальное в окне F1
_PAN_KEYS = {Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down}

_WASD = {Qt.Key_W: Qt.Key_Up, Qt.Key_A: Qt.Key_Left, Qt.Key_S: Qt.Key_Down, Qt.Key_D: Qt.Key_Right}  # как стрелки

_KB_MOUSE_EVENTS = (QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.Wheel)

def _is_action(it) -> bool:
    """Намерение геймпада — настоящее действие: кнопка или стик дальше мёртвой зоны (центр стика — нет)."""
    if isinstance(it, PanTilt):
        return bool(it.vx or it.vy)
    if isinstance(it, Zoom):
        return bool(it.v)
    return not isinstance(it, Disconnected)

KEY_PAN_FAST = 1.0  # Shift + стрелки / WASD: полный наклон, как упор стика

_MENU_KEYS = {Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Return, Qt.Key_Enter}

_ZOOM_IN_KEYS = {Qt.Key_Plus, Qt.Key_Equal}

_ZOOM_OUT_KEYS = {Qt.Key_Minus, Qt.Key_Underscore}

_PAD_SIZES = ("waist", "full", "wide")  # L1/R1: только крупность, от ближнего к общему

def _clamp1(v: float) -> float:
    return max(-1.0, min(1.0, v))

QWIDGETSIZE_MAX = (1 << 24) - 1  # «без предела» по высоте, как в Qt

STRIP_MS = 80  # снимки в полоске «1 камера» — ~12 кадров в секунду (пользователь 2026-11-06: хотя бы 10–15)

STRIP_THUMB_H = 72

CYCLE_S = 30.0  # «По очереди» в свободной клетке: смена вида (пользователь 2026-11-05)

CYCLE_KINDS = ("summary", "position")

SERVICE_CHOICES = [("svc:summary", "Сводка"), ("svc:position", "Положение камер"),
                   ("svc:graphs", "Графики PTZ"), ("svc:cycle", "По очереди · 30 с")]

SIDE_FIT_RESERVE = 80  # окно 1640×850 на Mac: ~70 px свободно; стик растёт только сверх этого (запас под шрифт Windows)

def _vline() -> QFrame:
    """Тонкий разделитель групп в панелях."""
    f = QFrame()
    f.setFixedSize(1, 18)
    f.setStyleSheet(f"background: {C.LINE};")
    return f
