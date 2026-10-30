"""Меню камеры поверх видео (кнопка Options геймпада, клавиша M). Не диалог: окно и камера живут как обычно.

Фокус и ирис — через флажки правой панели (одна дорога к камере); gain и баланс белого — методы сессии,
значения читаем из `session.image`, туда же смотрит и правая панель."""
from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ptztracker.camera import protocol as P
from ptztracker.ui.theme import FS_CAPTION, FS_TEXT, R_BUTTON, R_PANEL, SP, C, _rgba

AUTOCLOSE_S = 10.0  # без нажатий меню закрывается само
FOCUS_SPEED = 0.3  # ближе/дальше, пока держишь ←/→
CTEMP_STEP = 2  # шаг цветовой температуры за нажатие (≈200 K)
LOCAL_HOLD_S = 1.5  # столько держим только что выбранное значение, пока камера не ответила
WB_CHOICES = (0, 1, 2, 4, 5, 9)  # ATW, AWB A, AWB B, 3200K, 5600K, VAR
ROW_H = 36
WIDTH = 340


class _Row(QWidget):
    clicked = Signal(str)

    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self.setFixedHeight(ROW_H)
        self.setFocusPolicy(Qt.NoFocus)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP[2], 0, SP[2], 0)
        self.title = QLabel()
        self.value = QLabel()
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.title, 1)
        lay.addWidget(self.value, 1)

    def style(self, selected: bool, enabled: bool) -> None:
        bg = _rgba(C.ACCENT, 0.24) if selected else "transparent"
        border = C.ACCENT if selected else "transparent"
        self.setStyleSheet(f"_Row {{ background: {bg}; border: 1px solid {border}; border-radius: {R_BUTTON}px; }}")
        col = C.TEXT if enabled else C.TEXT_FAINT
        self.title.setStyleSheet(f"color: {col};")
        self.value.setStyleSheet(f"color: {C.TEXT if enabled else C.TEXT_FAINT}; font-weight: 700;")

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key)


class CameraMenu(QFrame):
    openChanged = Signal(bool)

    def __init__(self, session: Callable[[], object | None], controls, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = session
        self._controls = controls
        self.is_open = False  # читает поток опроса геймпада: булево, чтение атомарно
        self._cursor = "focus"
        self._focus_moving = False
        self._local: dict[str, tuple[int, float]] = {}
        self.setObjectName("cameraMenu")
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedWidth(WIDTH)
        self.setStyleSheet(
            f"#cameraMenu {{ background: {_rgba(C.SURFACE, 0.94)}; border: 1px solid {C.LINE};"
            f" border-radius: {R_PANEL}px; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[1], SP[1], SP[1], SP[1])
        lay.setSpacing(2)
        head = QLabel("Меню камеры")
        head.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px; padding: 2px {SP[2]}px;")
        lay.addWidget(head)
        self._rows: dict[str, _Row] = {}
        for key in ("focus", "wb", "ctemp", "iris", "gain"):
            r = _Row(key)
            r.clicked.connect(self.click_item)
            self._rows[key] = r
            lay.addWidget(r)
        foot = QLabel("↑↓ пункт   ←→ значение   ✕ принять   ○ закрыть")
        foot.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px; padding: 2px {SP[2]}px;")
        lay.addWidget(foot)
        self.setStyleSheet(self.styleSheet() + f" QLabel {{ font-size: {FS_TEXT}px; }}")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._tick = QTimer(self)  # значения камеры приходят позже команды — перерисовка, пока меню открыто
        self._tick.setInterval(250)
        self._tick.timeout.connect(self._render)
        self.hide()

    # --- состояние пунктов -------------------------------------------------

    def _s(self):
        return self._session()

    def _has(self, *names: str) -> bool:
        s = self._s()
        return s is not None and all(callable(getattr(s, n, None)) for n in names)

    def _cur(self, key: str, value: int | None) -> int | None:
        loc = self._local.get(key)
        if loc is not None and time.monotonic() - loc[1] < LOCAL_HOLD_S:
            return loc[0]
        return value

    def _remember(self, key: str, value: int) -> None:
        self._local[key] = (value, time.monotonic())

    def _image(self):
        return getattr(self._s(), "image", None)

    def _gain(self) -> int | None:
        img = self._image()
        return self._cur("gain", getattr(img, "gain", None))

    def _wb(self) -> int | None:
        img = self._image()
        return self._cur("wb", getattr(img, "wb", None))

    def _ctemp(self) -> int | None:
        img = self._image()
        return self._cur("ctemp", getattr(img, "color_temp", None))

    def _enabled(self, key: str) -> bool:
        if key == "focus":
            return self._s() is not None
        if key == "iris":
            return self._s() is not None
        if key == "gain":
            return self._has("set_gain")
        if key == "wb":
            return self._has("set_wb")
        if key == "ctemp":
            return self._has("set_color_temp") and self._wb() == P.WB_VAR
        return False

    def visible_keys(self) -> list[str]:
        keys = ["focus", "wb"]
        if self._wb() == P.WB_VAR:
            keys.append("ctemp")
        return keys + ["iris", "gain"]

    def values(self) -> dict[str, str]:
        c = self._controls
        out = {
            "focus": "Авто" if c.af_check.isChecked() else "Ручной  ◀ ▶",
            "iris": "Авто" if c.iris_auto.isChecked() else "Ручной",
        }
        g = self._gain()
        out["gain"] = "—" if g is None else ("Авто" if g == P.GAIN_AUTO else f"Ручной  {P.gain_db(g)} dB")
        wb = self._wb()
        out["wb"] = "—" if wb is None else P.WB_MODES.get(wb, "?")
        ct = self._ctemp()
        out["ctemp"] = "—" if ct is None else f"≈{P.color_temp_kelvin(ct)} K  ◀ ▶"
        return out

    def _render(self) -> None:
        keys = self.visible_keys()
        if self._cursor not in keys or not self._enabled(self._cursor):
            self._cursor = next((k for k in keys if self._enabled(k)), keys[0])
        vals = self.values()
        titles = {"focus": "Фокус", "wb": "Баланс белого", "ctemp": "   Цвет. температура",
                  "iris": "Ирис", "gain": "Gain"}
        for k, r in self._rows.items():
            r.setVisible(k in keys)
            r.title.setText(titles[k])
            r.value.setText(vals[k])
            r.style(k == self._cursor, self._enabled(k))
        self.adjustSize()

    # --- открытие и закрытие ---------------------------------------------------

    def open(self) -> None:
        if not self.is_open:
            self.is_open = True
            self._cursor = "focus"
            self.show()
            self.raise_()
            self._tick.start()
            self.openChanged.emit(True)
        self._render()
        self._restart()

    def close_menu(self) -> None:
        self._stop_focus()
        self._timer.stop()
        self._tick.stop()
        if self.is_open:
            self.is_open = False
            self.hide()
            self.openChanged.emit(False)

    def toggle(self) -> None:
        self.close_menu() if self.is_open else self.open()

    def _restart(self) -> None:
        self._timer.start(int(AUTOCLOSE_S * 1000))

    def _on_timeout(self) -> None:
        if self._focus_moving:
            self._restart()  # оператор держит ←/→ — не закрываем
        else:
            self.close_menu()

    def _stop_focus(self) -> None:
        if self._focus_moving:
            self._focus_moving = False
            s = self._s()
            if s is not None:
                s.set_focus_velocity(0.0)

    # --- управление ------------------------------------------------------------

    def nav(self, dx: int, dy: int) -> None:
        if not self.is_open:
            return
        self._restart()
        if dx == 0 and dy == 0:
            self._stop_focus()  # отпустили ←/→
            return
        if dy:
            keys = [k for k in self.visible_keys() if self._enabled(k)]
            if keys:
                i = keys.index(self._cursor) if self._cursor in keys else -1
                self._cursor = keys[max(0, min(len(keys) - 1, i + dy)) if i >= 0 else 0]
            self._render()
            return
        self._horizontal(dx)
        self._render()

    def _horizontal(self, d: int) -> None:
        k = self._cursor
        if not self._enabled(k):
            return
        if k == "focus":
            if not self._controls.af_check.isChecked():
                self._focus_moving = True
                self._s().set_focus_velocity(FOCUS_SPEED * d)
        elif k == "wb":
            self._wb_step(d)
        elif k == "ctemp":
            self._ctemp_step(d)
        else:
            self._toggle(k)

    def accept(self) -> None:
        if not self.is_open:
            return
        self._restart()
        k = self._cursor
        if self._enabled(k):
            if k == "wb":
                self._wb_step(+1)
            elif k != "ctemp":
                self._toggle(k)
        self._render()

    def click_item(self, key: str) -> None:
        if self.is_open and self._enabled(key):
            self._cursor = key
            self.accept()

    def _toggle(self, k: str) -> None:
        c = self._controls
        if k == "focus":
            self._stop_focus()
            c.af_check.setChecked(not c.af_check.isChecked())  # toggled → session.set_focus_auto
        elif k == "iris":
            c.iris_auto.setChecked(not c.iris_auto.isChecked())
        elif k == "gain":
            g = self._gain()
            s = self._s()
            if g is None:
                return
            if g == P.GAIN_AUTO:
                code = P.GAIN_CODES[c.gain_slider.value()]
            else:
                code = P.GAIN_AUTO
            s.set_gain(code)
            self._remember("gain", code)

    def _wb_step(self, d: int) -> None:
        wb = self._wb()
        if wb is None:
            return
        i = WB_CHOICES.index(wb) if wb in WB_CHOICES else 0
        new = WB_CHOICES[max(0, min(len(WB_CHOICES) - 1, i + d))]
        if new != wb:
            self._s().set_wb(new)
            self._remember("wb", new)

    def _ctemp_step(self, d: int) -> None:
        ct = self._ctemp()
        if ct is None:
            return
        new = max(0, min(P.COLOR_TEMP_MAX, ct + d * CTEMP_STEP))
        if new != ct:
            self._s().set_color_temp(new)
            self._remember("ctemp", new)


__all__ = ["AUTOCLOSE_S", "CameraMenu"]
