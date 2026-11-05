"""Меню камеры поверх видео (кнопка Options геймпада, клавиша M). Не диалог: окно и камера живут как обычно.

Фокус и ирис — через флажки правой панели (одна дорога к камере); gain и баланс белого — методы сессии,
значения читаем из `session.image`, туда же смотрит и правая панель."""
from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ptztracker.camera import protocol as P
from ptztracker.ui.theme import FS_CAPTION, FS_TEXT, R_BUTTON, R_PANEL, SP, C, _rgba

AUTOCLOSE_S = 10.0  # без нажатий меню закрывается само
FOCUS_MAX_S = 3.0  # ручной фокус за одно нажатие едет не дольше (потеря отпускания не страшна)
FOCUS_SPEED = 0.3  # ближе/дальше, пока держишь ←/→
CTEMP_STEP = 2  # шаг цветовой температуры за нажатие (≈200 K)
LOCAL_HOLD_S = 1.5  # столько держим только что выбранное значение, пока камера не ответила
WB_CHOICES = (0, 1, 2, 4, 5, 9)  # ATW, AWB A, AWB B, 3200K, 5600K, VAR
ROW_H = 26  # девять пунктов и пояснение помещаются в клетку 2×2 окна 1640×850
WIDTH = 380


class _Row(QWidget):
    clicked = Signal(str)

    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self.setAttribute(Qt.WA_StyledBackground, True)  # иначе рамка выбранного пункта из QSS не рисуется
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
        bg = _rgba(C.TEXT, 0.06) if selected else "transparent"
        border = C.TEXT if selected else "transparent"
        self.setStyleSheet(f"_Row {{ background: {bg}; border: 1px solid {border}; border-radius: {R_BUTTON}px; }}")
        col = C.TEXT if enabled else C.TEXT_FAINT
        self.title.setStyleSheet(f"color: {col};")
        self.value.setStyleSheet(f"color: {C.TEXT if enabled else C.TEXT_FAINT}; font-weight: 700;")

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key)


# редкие настройки: пункт → метод сессии. Камера HE40/HE42 в Full Auto отвечает ER3 — значение остаётся прежним
RARE = {"shutter": "set_shutter", "synchro": "set_synchro", "pedestal": "set_pedestal", "detail": "set_detail"}
# «зачем» — подсказка при наведении и строка внизу меню у выбранного пункта (геймпад мышью не наводит)
WHY = {
    "focus": "Авто — камера сама держит резкость; Ручной — ←→ ближе/дальше, если автофокус «плавает».",
    "wb": "Цвет белого: ATW — подстраивается сам, AWB A/B — запомненный, 3200K/5600K — под лампы и дневной свет.",
    "ctemp": "Вручную подогнать цвет под свет на сцене (режим VAR).",
    "iris": "Сколько света пускает объектив. Авто — камера сама держит яркость.",
    "gain": "Усиление сигнала: ярче в тёмном зале, но больше шума. Авто — камера решает сама.",
    "shutter": "Убрать мерцание LED-экранов и ламп на сцене. Synchro — точная подстройка под частоту экрана.",
    "synchro": "Крутите, пока полосы на LED-экране не исчезнут.",
    "pedestal": "Уровень чёрного: подогнать картинку под соседние камеры, чтобы при переключении не было скачка.",
    "detail": "Резкость контуров: подогнать под соседние камеры; «Высокая» делает лица жёстче.",
    "power": "Камера в режиме ожидания. × или → — включить (видео появится секунд через 8).",
    "scene": "Сцена камеры. Full Auto — камера сама держит ирис, gain и баланс белого, вручную их не поменять;"
             " Manual 1–3 — можно настраивать.",
    "standby": "Перевести камеру в режим ожидания: видео пропадёт, включение — около 8 секунд."
               " Нажмите × два раза — защита от случайного нажатия.",
    "osd": "Всё остальное, что умеет камера, — в её собственном меню Panasonic. Оно появляется в видео камеры"
           " (у камеры в эфире — и в эфире).",
}
OSD_AUTOCLOSE_S = 60.0
TITLE_QUICK = "Быстрые настройки"
TITLE_OSD = "Меню Panasonic на камере"
KEYS_QUICK = "↑↓ пункт · ←→ значение · ○ закрыть"
KEYS_OSD = "↑↓  пункт\n→  выбрать\n←  назад\n○  закрыть"
OSD_WIDTH = 150  # в меню Panasonic — узкая плашка слева: середину кадра занимает меню самой камеры
ORDER = ("power", "focus", "wb", "ctemp", "iris", "gain", "shutter", "synchro", "pedestal", "detail", "scene",
         "standby", "osd")
CONFIRM_S = 3.0  # «Выключить камеру»: второе нажатие × должно прийти за это время
REFUSED_HINT = ("Камера не принимает эту настройку: она в режиме Full Auto. Выберите «Сцена» → Manual 1"
                " ниже — тогда настройки заработают.")
OSD_HINT = "Меню рисует сама камера — смотрите на картинку этой клетки. ○ или Esc — закрыть меню камеры."
HZ50 = True  # ⚠ сеть 50 Гц (Россия): подписи затвора и частоты Synchro; формат камеры пока не спрашиваем


class CameraMenu(QFrame):
    openChanged = Signal(bool)
    placementChanged = Signal(str)  # center | left — где меню поверх видео

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
        # «Быстрые настройки» — наше окошко в программе (в эфир не попадает); «Меню Panasonic» — меню самой
        # камеры, которое она рисует в своё видео (пользователь 2026-11-05: не путать два меню)
        # название слева, клавиши справа мелко — раньше одной строкой, и клавиши обрезались
        self.title_label = QLabel(TITLE_QUICK)
        self.title_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px; font-weight: 600;")
        self.keys_label = QLabel(KEYS_QUICK)
        self.keys_label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION - 1}px;")
        self.keys_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head = self._head = QHBoxLayout()
        head.setContentsMargins(SP[2], 2, SP[2], 2)
        head.addWidget(self.title_label)
        head.addStretch(1)
        head.addWidget(self.keys_label)
        lay.addLayout(head)
        self.osd_mode = False
        self.on_air: Callable[[], bool] = lambda: False  # окно подставляет: камера меню сейчас в PGM
        self.on_power_on: Callable[[], None] = lambda: None  # окно подставляет: включить камеру меню
        self._placement = "center"
        self._confirm_until = 0.0  # «Выключить камеру»: ждём второе нажатие до этого момента
        self._rows: dict[str, _Row] = {}
        for key in ORDER:
            r = _Row(key)
            r.clicked.connect(self.click_item)
            self._rows[key] = r
            lay.addWidget(r)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self._hint_color = None
        self._set_hint_color(C.TEXT_DIM)
        lay.addWidget(self.hint)
        self.setStyleSheet(self.styleSheet() + f" QLabel {{ font-size: {FS_TEXT}px; }}")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.timeout.connect(self._stop_focus)
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

    def _supports(self, feature: str) -> bool:
        """Камера не отвечала ER1 на эту возможность (у сессий без supports — считаем, что умеет)."""
        sup = getattr(self._s(), "supports", None)
        return sup(feature) if callable(sup) else True

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

    def _power(self) -> str:
        p = getattr(self._s(), "power", "on")
        return p if isinstance(p, str) else "on"

    def _asleep(self) -> bool:
        return self._power() in ("standby", "switching")

    def _enabled(self, key: str) -> bool:
        if key == "power":
            return self._power() == "standby"
        if self._asleep():
            return False
        if key == "focus":
            return self._s() is not None
        if key == "iris":
            return self._s() is not None
        if key == "gain":
            return self._has("set_gain") and self._supports("gain")
        if key == "wb":
            return self._has("set_wb") and self._supports("wb")
        if key == "ctemp":
            return (self._has("set_color_temp") and self._supports("wb") and self._supports("color_temp")
                    and self._wb() == P.WB_VAR)
        if key in RARE:
            return self._has(RARE[key]) and self._val(key) is not None
        if key == "osd":
            return self._has("set_osd", "osd_key")
        if key == "scene":
            return self._has("set_scene") and self._val("scene") is not None
        if key == "standby":
            return self._has("set_power") and self._power() == "on"
        return False

    def visible_keys(self) -> list[str]:
        if self._asleep():  # камера спит — в меню только «Включить камеру» (геймпаду больше нечем её включить)
            return ["power"]
        keys = ["focus", "wb"]
        if self._wb() == P.WB_VAR:
            keys.append("ctemp")
        keys += ["iris", "gain"]
        # редкие настройки (спека 2026-11-05): затвор, Synchro — только когда выбран, уровень чёрного, резкость
        if self._has("set_shutter"):
            keys.append("shutter")
            if self._val("shutter") == P.SHUTTER_SYNCHRO:
                keys.append("synchro")
        if self._has("set_pedestal"):
            keys.append("pedestal")
        if self._has("set_detail"):
            keys.append("detail")
        if self._has("set_scene") and self._val("scene") is not None:
            keys.append("scene")
        if self._has("set_power"):
            keys.append("standby")
        if self._has("set_osd"):
            keys.append("osd")
        return keys

    def _val(self, key: str) -> int | None:
        return self._cur(key, getattr(self._image(), key, None))

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
        sh, sy, pd, dt = (self._val(k) for k in ("shutter", "synchro", "pedestal", "detail"))
        out["shutter"] = "—" if sh is None else f"{P.shutter_label(sh, HZ50)}  ◀ ▶"
        out["synchro"] = "—" if sy is None else f"{P.synchro_hz(sy, HZ50):.1f} Гц  ◀ ▶".replace(".", ",")
        out["pedestal"] = "—" if pd is None else f"{pd:+d}  ◀ ▶".replace("+0", "0")
        out["detail"] = "—" if dt is None else P.DETAIL_MODES.get(dt, "?")
        out["osd"] = "›"
        out["power"] = "включается…" if self._power() == "switching" else "×"
        sc = self._val("scene")
        out["scene"] = "—" if sc is None else f"{P.SCENES.get(sc, sc)}  ◀ ▶"
        if time.monotonic() < self._confirm_until:
            out["standby"] = ("В ЭФИРЕ! × ещё раз" if self.on_air() else "× ещё раз — выключить")
        else:
            out["standby"] = "×"
        return out

    def _set_hint_color(self, color: str) -> None:
        if color != self._hint_color:
            self._hint_color = color
            self.hint.setStyleSheet(f"color: {color}; font-size: {FS_CAPTION}px; padding: 4px {SP[2]}px 0;")

    def _refused(self) -> bool:
        f = getattr(self._s(), "refused_recently", None)
        return bool(f()) if callable(f) else False

    def _render(self) -> None:
        keys = self.visible_keys()
        if self._cursor not in keys or not self._enabled(self._cursor):
            # пункт пропал (например, «Цвет. температура» после смены баланса белого): ближайший выше, не первый
            i = ORDER.index(self._cursor) if self._cursor in ORDER else 0
            above = [k for k in ORDER[:i] if k in keys and self._enabled(k)]
            self._cursor = above[-1] if above else next((k for k in keys if self._enabled(k)), keys[0])
        vals = self.values()
        titles = {"scene": "Сцена", "standby": "Выключить камеру", "power": "Включить камеру", "focus": "Фокус", "wb": "Баланс белого", "ctemp": "   Цвет. температура",
                  "iris": "Ирис", "gain": "Gain", "shutter": "Затвор", "synchro": "   Частота Synchro",
                  "pedestal": "Уровень чёрного", "detail": "Резкость", "osd": "Меню Panasonic на камере"}
        self.title_label.setText(TITLE_OSD if self.osd_mode else TITLE_QUICK)
        self.keys_label.setText(KEYS_OSD if self.osd_mode else KEYS_QUICK)
        # меню Panasonic: узкая плашка слева — название и клавиши столбиком, без пояснения
        self.setFixedWidth(OSD_WIDTH if self.osd_mode else WIDTH)
        self._head.setDirection(QBoxLayout.TopToBottom if self.osd_mode else QBoxLayout.LeftToRight)
        self.keys_label.setAlignment(Qt.AlignLeft if self.osd_mode else Qt.AlignRight | Qt.AlignVCenter)
        self.hint.setVisible(not self.osd_mode)
        self.title_label.setWordWrap(self.osd_mode)  # узкая плашка: «Меню Panasonic на камере» в две строки
        self.keys_label.setStyleSheet(
            f"color: {C.TEXT_DIM if self.osd_mode else C.TEXT_FAINT}; font-size: {FS_CAPTION - 1}px;"
            + (" line-height: 150%; padding-top: 4px;" if self.osd_mode else ""))
        placement = "left" if self.osd_mode else "center"
        if placement != self._placement:
            self._placement = placement
            self.placementChanged.emit(placement)
        for k, r in self._rows.items():
            r.setVisible(k in keys and not self.osd_mode)
            r.title.setText(titles[k])
            r.value.setText(vals[k])
            r.setToolTip(WHY[k])
            r.style(k == self._cursor, self._enabled(k))
        refused = not self.osd_mode and self._cursor not in ("focus", "iris", "osd") and self._refused()
        self._set_hint_color(C.LOST if refused else C.TEXT_DIM)
        self.hint.setText(OSD_HINT if self.osd_mode else REFUSED_HINT if refused else WHY.get(self._cursor, ""))
        # перенос строк: высоту считаем по ширине меню, иначе пояснение обрезается снизу
        m = self.layout().contentsMargins()
        self.hint.setFixedHeight(0 if self.osd_mode else self.hint.heightForWidth(WIDTH - m.left() - m.right()))
        self.adjustSize()

    def placement(self) -> str:
        return self._placement

    # --- открытие и закрытие ---------------------------------------------------

    def open(self) -> None:
        if not self.is_open:
            self.is_open = True
            self._cursor = "power" if self._asleep() else "focus"
            self.show()
            self.raise_()
            self._tick.start()
            self.openChanged.emit(True)
        self._render()
        self._restart()

    def close_menu(self) -> None:
        self._leave_osd()
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
        # по меню Panasonic ходят медленнее — минута без нажатий, а не AUTOCLOSE_S
        self._timer.start(int((OSD_AUTOCLOSE_S if self.osd_mode else AUTOCLOSE_S) * 1000))

    def _on_timeout(self) -> None:
        self.close_menu()  # close_menu сначала останавливает фокус

    def stop_focus(self) -> None:
        self._stop_focus()

    def _stop_focus(self) -> None:
        self._focus_timer.stop()
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
        if self.osd_mode:  # кнопки пульта камеры: ↑↓ — пункт, → — выбрать, ← — назад
            key = ("up" if dy < 0 else "down") if dy else ("enter" if dx > 0 else "back")
            self._s().osd_key(key)
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
        if k == "power":
            if d > 0:
                self.on_power_on()
            return
        if k == "standby":
            if d > 0:
                self._standby_press()
            return
        if k == "scene":
            self._scene_step(d)
            return
        self._light(k)
        if k == "focus":
            if not self._controls.af_check.isChecked():
                self._focus_moving = True
                self._s().set_focus_velocity(FOCUS_SPEED * d)
                self._focus_timer.start(int(FOCUS_MAX_S * 1000))
        elif k == "wb":
            self._wb_step(d)
        elif k == "ctemp":
            self._ctemp_step(d)
        elif k in RARE:
            self._rare_step(k, d)
        elif k == "osd":
            if d > 0:
                self._enter_osd()
        else:
            self._toggle(k)

    def accept(self) -> None:
        if not self.is_open:
            return
        self._restart()
        if self.osd_mode:
            self._s().osd_key("enter")
            return
        k = self._cursor
        if k == "power":
            if self._enabled(k):
                self.on_power_on()
            self._render()
            return
        if k == "standby":
            self._standby_press()
            self._render()
            return
        if self._enabled(k):
            self._light(k)
            if k == "osd":
                self._enter_osd()
            elif k == "wb":
                self._wb_step(+1)
            elif k in ("shutter", "detail"):
                self._rare_step(k, +1)
            elif k == "scene":
                self._scene_step(+1)
            elif k not in ("ctemp", "synchro", "pedestal"):
                self._toggle(k)
        self._render()

    def _light(self, key: str) -> None:
        """На правой панели загорается строка того, что меняют из меню (редких настроек там нет)."""
        light = getattr(self._controls, "light_row", None)
        if light is not None:
            light("wb" if key == "ctemp" else key)

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

    def _enter_osd(self) -> None:
        """Открыть меню Panasonic. У камеры в эфире — сначала спросить: меню увидят зрители."""
        if self.on_air():
            from PySide6.QtWidgets import QMessageBox

            q = ("Камера сейчас в эфире (PGM). Меню Panasonic камера рисует прямо в своё видео — его увидят"
                 " зрители.\n\nВсё равно открыть?")
            if QMessageBox.question(self.window(), "Меню Panasonic", q) != QMessageBox.Yes:
                return
        self._stop_focus()
        self._s().set_osd(True)
        self.osd_mode = True
        self._render()
        self._restart()

    def _standby_press(self) -> None:
        """Первое нажатие — «× ещё раз»; второе за CONFIRM_S — в режим ожидания (как ⋯ → режим ожидания)."""
        if not self._enabled("standby"):
            return
        now = time.monotonic()
        if now < self._confirm_until:
            self._confirm_until = 0.0
            self._s().set_power(False)
        else:
            self._confirm_until = now + CONFIRM_S
            QTimer.singleShot(int(CONFIRM_S * 1000) + 50, self._render)  # подпись вернётся к «×»

    def _scene_step(self, d: int) -> None:
        cur = self._val("scene")
        keys = sorted(P.SCENES)
        i = keys.index(cur) if cur in keys else 0
        new = keys[(i + (1 if d >= 0 else -1)) % len(keys)]
        if self.on_air():
            from PySide6.QtWidgets import QMessageBox

            q = "Камера сейчас в эфире (PGM). Смена сцены меняет картинку — зрители это увидят.\n\nСменить?"
            if QMessageBox.question(self.window(), "Сцена камеры", q) != QMessageBox.Yes:
                return
        self._remember("scene", new)
        self._s().set_scene(new)

    def _leave_osd(self) -> None:
        if self.osd_mode:
            self.osd_mode = False
            s = self._s()
            if s is not None:
                s.set_osd(False)

    def _rare_step(self, k: str, d: int) -> None:
        """Затвор и резкость — по кругу ступеней (клик/✕ — следующая); Synchro и уровень чёрного — шагом."""
        v = self._val(k)
        if v is None:
            return
        if k == "shutter":
            steps = list(P.SHUTTER_STEPS)
            new = steps[(steps.index(v) + d) % len(steps)] if v in steps else steps[0]
        elif k == "detail":
            new = (v + d) % len(P.DETAIL_MODES)
        elif k == "synchro":
            new = max(P.SYNCHRO_MIN, min(P.SYNCHRO_MAX, v + d))
        else:
            new = max(-10, min(10, v + d))
        if new != v:
            getattr(self._s(), RARE[k])(new)
            self._remember(k, new)

    def _ctemp_step(self, d: int) -> None:
        ct = self._ctemp()
        if ct is None:
            return
        new = max(0, min(P.COLOR_TEMP_MAX, ct + d * CTEMP_STEP))
        if new != ct:
            self._s().set_color_temp(new)
            self._remember("ctemp", new)


__all__ = ["AUTOCLOSE_S", "CameraMenu"]
