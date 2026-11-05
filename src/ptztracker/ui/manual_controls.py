from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ptztracker.camera import protocol as P
from ptztracker.camera.session import CameraSession, ImageState
from ptztracker.control.manual_curve import DEFAULT_SENS, SENS_MAX, SENS_MIN, clamp_sens, shape
from ptztracker.ui.camera_menu import WHY
from ptztracker.ui.joystick import JoystickWidget
from ptztracker.ui.theme import FS_CAPTION, H_STOP, SP, C, qcolor
from ptztracker.ui.widgets import KeyButton, SectionHeader, keycap_width, paint_keycap

PRESET_SIZE = 32
ZOOM_ROCKER_H = 112  # вместе с клавишами и подписями — по высоте стика
STICK_MIN, STICK_MAX = 176, 260  # стик: в окне 1640×850 и на высоком экране (MacBook Pro 16 во весь экран)
LABEL_W = 52  # колонка подписей «Изображения» (спека «Всё влезает и стоит ровно»)
H_SMALL = 28  # кнопки фокуса
ROW_LIT_S = 1.5  # столько горит строка после действия оператора (мышь, клавиши, быстрые настройки)
TOUCH_HOLD_S = 1.5  # столько опрос камеры не перебивает ползунок, который оператор только что тронул


class PanelRow(QWidget):
    """Строка панели как в таблице: через одну — чуть светлее фон; строка, которую оператор сейчас крутит,
    горит — лаймовая полоска слева и светлее фон (пользователь 2026-11-05, вариант «зебра + полоска»)."""

    PAD = SP[0]

    def __init__(self, content, shaded: bool, label: QLabel | None = None):
        super().__init__()
        self.shaded = shaded
        self.active = False
        self._label = label
        # общий стиль красит каждый виджет в фон окна — внутри строки фон должен быть её собственный
        self.setStyleSheet("QLabel, QCheckBox, QSlider { background: transparent; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(self.PAD + 2, self.PAD, self.PAD, self.PAD)
        lay.setSpacing(SP[0])
        for part in content if isinstance(content, (list, tuple)) else (content,):
            if isinstance(part, QWidget):
                lay.addWidget(part)
            else:
                lay.addLayout(part)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._set_active(False))

    def flash(self) -> None:
        self._set_active(True)
        self._timer.start(int(ROW_LIT_S * 1000))

    def _set_active(self, on: bool) -> None:
        if on == self.active:
            return
        self.active = on
        if self._label is not None:
            self._label.setStyleSheet(f"color: {C.TEXT if on else C.TEXT_DIM};")
        self.update()

    def paintEvent(self, e):  # noqa: N802
        if not (self.shaded or self.active):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        bg = qcolor(C.LIME if self.active else C.TEXT)  # горящая строка — с лаймовым оттенком, как в макете
        bg.setAlphaF(0.08 if self.active else 0.035)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        if self.active:
            p.setBrush(qcolor(C.LIME))
            p.drawRoundedRect(QRectF(0, 6, 3, self.height() - 12), 1.5, 1.5)
        p.end()


class SpringSlider(QSlider):
    """Ползунок «держи-и-тяни»: колесо и клавиатура его не двигают, отпускание мыши возвращает в 0.

    Иначе случайная прокрутка над ним запускала бы зум, который ничто не останавливает.
    """

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.NoFocus)

    def wheelEvent(self, e):  # noqa: N802
        e.ignore()

    def keyPressEvent(self, e):  # noqa: N802
        e.ignore()

    def mouseReleaseEvent(self, e):  # noqa: N802
        super().mouseReleaseEvent(e)
        self.setValue(0)


class ManualControls(QWidget):
    """Ручное управление: джойстик, зум, ирис, gain, пресеты, фокус, баланс белого, стоп.
    Gain и баланс белого — настройка картинки: слежение не выключают (как ирис)."""

    stopRequested = Signal()
    sensitivityChanged = Signal(int)  # мышь и стрелки: 0 — «Плавно», 100 — «Резко»
    slowTeleChanged = Signal(bool)  # «Медленнее на крупном плане» (мышь, стрелки и геймпады)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session: CameraSession | None = None

        self.joystick = JoystickWidget()
        self.joystick.moved.connect(self._on_joystick)

        # зум — качелька справа от стика, как на пульте PTZ: вверх — ближе, вниз — шире
        self.zoom_slider = SpringSlider(Qt.Vertical)
        self.zoom_slider.setRange(-100, 100)
        self.zoom_slider.setFixedHeight(ZOOM_ROCKER_H)
        # пружинный: в покое нейтральный, без акцентной заливки; метка по центру
        self.zoom_slider.setTickPosition(QSlider.TicksRight)
        self.zoom_slider.setTickInterval(100)
        self.zoom_slider.setStyleSheet(
            f"QSlider::sub-page:vertical, QSlider::add-page:vertical {{ background: {C.RAISED_HI};"
            " border-radius: 3px; }"
        )
        self.zoom_slider.setToolTip("Тяните вверх — приблизить, вниз — отдалить. Отпустите — стоп.")
        self.zoom_slider.valueChanged.connect(self._on_zoom)
        self.zoom_slider.sliderReleased.connect(lambda: self.zoom_slider.setValue(0))
        # клавиша и подпись в одну строку: колонка не выше стика (крупный шрифт Windows)
        self._zoom_col = zoom_col = QVBoxLayout()
        zoom_col.setSpacing(SP[0])
        for key, text in (("+", "Ближе"), (None, None), ("−", "Шире")):
            if key is None:
                zoom_col.addWidget(self.zoom_slider, 0, Qt.AlignHCenter)
                continue
            row = QHBoxLayout()
            row.setSpacing(SP[0])
            row.addWidget(self._keycap_label(key))
            row.addWidget(self._caption(text))
            zoom_col.addLayout(row)

        stick_row = QHBoxLayout()
        stick_row.addStretch(1)
        stick_row.addWidget(self.joystick, 0, Qt.AlignVCenter)
        stick_row.addSpacing(SP[3] * 2)
        stick_row.addLayout(zoom_col)
        stick_row.addStretch(1)

        # чувствительность стика под ним: форма кривой «наклон → скорость», полный наклон — полная скорость
        self.sensitivity = DEFAULT_SENS
        self.sens_slider = QSlider(Qt.Horizontal)
        self.sens_slider.setRange(SENS_MIN, SENS_MAX)
        self.sens_slider.setValue(DEFAULT_SENS)
        self.sens_slider.setPageStep(10)
        self.sens_slider.setFocusPolicy(Qt.NoFocus)  # стрелки — камере, не ползунку
        self.sens_slider.setTracking(False)  # значение — по отпусканию: config.json не пишется на каждый шаг
        self.sens_slider.setToolTip(
            "Чувствительность мыши и стрелок. «Плавно» — мягче у центра, легче сделать маленький поворот;"
            " «Резко» — как раньше. Полный наклон всегда даёт полную скорость.\n"
            "У каждого геймпада своя чувствительность: ☰ → «Геймпады…»")
        self.sens_slider.valueChanged.connect(self._on_sens)
        self.slow_tele_check = QCheckBox("Медленнее на крупном плане")
        self.slow_tele_check.setChecked(True)
        self.slow_tele_check.setFocusPolicy(Qt.NoFocus)
        self.slow_tele_check.setToolTip(
            "На крупном плане тот же наклон стика поворачивает камеру медленнее — кадр не улетает."
            " Мышь, стрелки и геймпады; слежение это не меняет.")
        self.slow_tele_check.toggled.connect(self.slowTeleChanged)
        # две строки: в одну с флажком не помещается по ширине панели на Windows (шрифт шире)
        sens_row = QHBoxLayout()
        sens_row.setSpacing(SP[0])
        sens_row.addWidget(QLabel("Чувствительность"))
        sens_row.addSpacing(SP[1])
        sens_row.addWidget(self._caption("Плавно"))
        sens_row.addWidget(self.sens_slider, 1)
        sens_row.addWidget(self._caption("Резко"))
        self._sens_row = sens_row

        self.stop_btn = KeyButton("СТОП КАМЕРЫ", "Esc", variant="stop")
        self.stop_btn.setFixedHeight(H_STOP)
        self.stop_btn.setFocusPolicy(Qt.NoFocus)
        self.stop_btn.pressed.connect(self.stopRequested)  # стоп на нажатии, без ожидания отпускания

        self.af_check = QCheckBox("Автофокус")
        self.af_check.setChecked(True)
        self.af_check.toggled.connect(lambda on: self._s and self._s.set_focus_auto(on))
        self.push_af = QPushButton("Разовый AF")
        self.push_af.clicked.connect(lambda: self._s and self._s.push_focus())
        self.near_btn = QPushButton("Ближе")
        self.far_btn = QPushButton("Дальше")
        self._focus_moving = False
        self.near_btn.pressed.connect(lambda: self._focus(-0.5))
        self.far_btn.pressed.connect(lambda: self._focus(0.5))
        for b in (self.near_btn, self.far_btn):
            b.released.connect(lambda: self._focus(0.0))
        self.af_check.setText("AF")
        self.af_check.setToolTip("Автофокус")
        self.push_af.setText("Разовый")
        self.push_af.setToolTip("Разовый автофокус")
        # фокус в две строки: «AF · Разовый», затем «Ближе ‹ползунок› Дальше»: ползунок пружинный, как зум —
        # тянете в сторону, фокус едет, отпустили — стоп; кнопки тоже работают, пока держите
        self.focus_slider = SpringSlider(Qt.Horizontal)
        self.focus_slider.setRange(-100, 100)
        self.focus_slider.setStyleSheet(
            f"QSlider::sub-page:horizontal, QSlider::add-page:horizontal {{ background: {C.RAISED_HI};"
            " border-radius: 1px; }")
        self.focus_slider.setToolTip("Тяните влево — фокус ближе, вправо — дальше. Отпустите — стоп.")
        self.focus_slider.valueChanged.connect(lambda v: self._focus(v / 200))  # полный ход = как кнопка (±0,5)
        self.focus_slider.sliderReleased.connect(lambda: self.focus_slider.setValue(0))
        fl = self._row("Фокус", self.push_af)  # «Разовый» первым, AF — рядом (пользователь 2026-11-05)
        fl.addSpacing(SP[2])
        fl.addWidget(self.af_check)
        fl.addStretch(1)
        fbl = QHBoxLayout()
        fbl.setSpacing(SP[1])
        fbl.addSpacing(LABEL_W + SP[1])
        for wd in (self.push_af, self.near_btn, self.far_btn):  # одна высота и вид у всех кнопок фокуса
            wd.setStyleSheet(f"QPushButton {{ min-height: {H_SMALL - 4}px; max-height: {H_SMALL - 4}px;"
                             f" padding: 0 {SP[1] + 2}px; font-size: {FS_CAPTION}px; }}")
        fbl.addWidget(self.near_btn)
        fbl.addWidget(self.focus_slider, 1)
        fbl.addWidget(self.far_btn)

        self.iris_auto = QCheckBox("Авто")
        self.iris_auto.setChecked(True)
        self.iris_slider = QSlider(Qt.Horizontal)
        self.iris_slider.setRange(0, 100)
        self.iris_slider.setValue(50)
        self.iris_slider.setEnabled(False)
        self.iris_auto.toggled.connect(self._on_iris_auto)
        self.iris_slider.valueChanged.connect(self._on_iris)
        il = self._row("Ирис", self.iris_auto, self.iris_slider)

        # gain: шаги по 3 dB (коды GAIN_CODES) и «Авто»
        self._touched: dict[str, float] = {}  # что оператор трогал недавно — опрос камеры это не перебивает
        self.gain_auto = QCheckBox("Авто")
        self.gain_slider = QSlider(Qt.Horizontal)
        self.gain_slider.setRange(0, len(P.GAIN_CODES) - 1)
        self.gain_slider.setPageStep(1)
        self.gain_value = QLabel("")
        self.gain_value.setMinimumWidth(48)
        self.gain_auto.toggled.connect(self._on_gain_auto)
        self.gain_slider.valueChanged.connect(self._on_gain)
        gl = self._row("Gain", self.gain_auto, self.gain_slider, self.gain_value)
        self._gain_row = gl

        # баланс белого: режим; температура — только в режиме VAR
        self.wb_combo = QComboBox()
        for mode, label in P.WB_MODES.items():
            self.wb_combo.addItem(label, mode)
        self.wb_combo.setToolTip("Баланс белого: ATW — авто, AWB A/B — запомненные, 3200K/5600K, VAR — вручную")
        # баланс белого — чип размером с «Разовый»: текст и галочка, без широкого поля
        self.wb_combo.setStyleSheet(f"QComboBox {{ min-height: {H_SMALL - 4}px; max-height: {H_SMALL - 4}px;"
                                    f" padding: 0 {SP[3] + SP[0]}px 0 {SP[1] + 2}px; font-size: {FS_CAPTION}px; }}")
        self.wb_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.wb_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.ctemp_slider = QSlider(Qt.Horizontal)
        self.ctemp_slider.setRange(0, P.COLOR_TEMP_MAX)
        self.ctemp_slider.setEnabled(False)
        self.ctemp_value = QLabel("")
        self.ctemp_value.setMinimumWidth(56)
        self.ctemp_value.setToolTip("Кельвины приблизительно: шкала камеры не сверена с её меню")
        self.wb_combo.currentIndexChanged.connect(self._on_wb)
        self.ctemp_slider.valueChanged.connect(self._on_ctemp)
        wl = self._row("WB", self.wb_combo, self.ctemp_slider, self.ctemp_value)
        self._wb_row = wl

        pl = QHBoxLayout()
        pl.setSpacing(SP[0])
        self.preset_buttons: list[QPushButton] = []
        for i in range(9):
            b = QPushButton(str(i + 1))
            b.setFixedSize(PRESET_SIZE, PRESET_SIZE)
            b.setStyleSheet(
                f"QPushButton {{ min-height: {PRESET_SIZE}px; max-height: {PRESET_SIZE}px;"
                f" min-width: {PRESET_SIZE}px; max-width: {PRESET_SIZE}px; padding: 0;"
                f" border-radius: {PRESET_SIZE // 2}px; }}")
            b.clicked.connect(lambda _=False, n=i + 1: self._preset_clicked(n))
            pl.addWidget(b)
            self.preset_buttons.append(b)

        # «зачем» при наведении — те же пояснения, что в меню камеры
        for row, key, extra in ((il, "iris", (self.iris_slider, self.iris_auto)),
                                (gl, "gain", (self.gain_slider, self.gain_auto)),
                                (fl, "focus", (self.af_check,)), (wl, "wb", ())):
            for wd in (row.itemAt(0).widget(), *extra):
                wd.setToolTip(WHY[key])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP[1])
        lay.addStretch(1)
        self.camera_header = SectionHeader("Камера", "движение выключает слежение")
        lay.addWidget(self.camera_header)
        lay.addLayout(stick_row)
        # строки — как в таблице (зебра); отступ между ними — внутри строк, чтобы фон шёл сплошной полосой
        self.rows: dict[str, PanelRow] = {
            "sens": PanelRow(sens_row, True, sens_row.itemAt(0).widget()),
            "slow_tele": PanelRow(self.slow_tele_check, False),
        }
        cam_rows = QVBoxLayout()
        cam_rows.setSpacing(0)
        cam_rows.addWidget(self.rows["sens"])
        cam_rows.addWidget(self.rows["slow_tele"])
        lay.addLayout(cam_rows)
        lay.addWidget(self.stop_btn)
        lay.addStretch(1)  # высокий экран: лишняя высота — поровну между разделами, в 1640×850 — ноль
        self.image_header = SectionHeader("Изображение", "не выключает слежение")
        lay.addWidget(self.image_header)
        img_rows = QVBoxLayout()
        img_rows.setSpacing(0)
        for i, (key, part) in enumerate((("iris", il), ("gain", gl), ("focus", (fl, fbl)), ("wb", wl))):
            first = part[0] if isinstance(part, tuple) else part
            self.rows[key] = PanelRow(part, i % 2 == 1, first.itemAt(0).widget())
            img_rows.addWidget(self.rows[key])
        lay.addLayout(img_rows)
        # строка загорается только от действия оператора: опрос камеры двигает ползунки сам и её не трогает
        lit = self.light_row
        for sl, key in ((self.iris_slider, "iris"), (self.gain_slider, "gain"), (self.ctemp_slider, "wb"),
                        (self.sens_slider, "sens"), (self.focus_slider, "focus")):
            sl.actionTriggered.connect(lambda _a, k=key: lit(k))
            sl.sliderPressed.connect(lambda k=key: lit(k))
        for cb, key in ((self.iris_auto, "iris"), (self.gain_auto, "gain"), (self.af_check, "focus"),
                        (self.slow_tele_check, "slow_tele")):
            cb.clicked.connect(lambda _c, k=key: lit(k))
        for b in (self.push_af, self.near_btn, self.far_btn):
            b.pressed.connect(lambda: lit("focus"))
        self.wb_combo.activated.connect(lambda _i: lit("wb"))
        # пресеты — в выдвижной панели снизу окна (спека «Выдвижная панель „Пресеты“»); кнопки остаются
        # объектами для клавиш и API, но в правой колонке их нет
        for b in self.preset_buttons:
            b.setParent(self)
            b.hide()
        self.bind(None)
        self._show_values()

    def light_row(self, key: str) -> None:
        """Подсветить строку, которую оператор сейчас меняет (мышь, клавиши, быстрые настройки, геймпад)."""
        row = self.rows.get(key)
        if row is not None:
            row.flash()

    @staticmethod
    def _row(title: str, *widgets: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SP[1])
        lab = QLabel(title)
        lab.setFixedWidth(LABEL_W)  # подписи одной колонкой: Ирис, Gain, Фокус, WB
        lab.setStyleSheet(f"color: {C.TEXT_DIM};")
        row.addWidget(lab)
        for wd in widgets:
            row.addWidget(wd, 1 if isinstance(wd, QSlider) else 0)
        return row

    @staticmethod
    def _caption(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        return lab

    @staticmethod
    def _keycap_label(text: str) -> QWidget:
        """Клавиша-подсказка (только рисунок)."""

        class _Cap(QWidget):
            def paintEvent(self, e):  # noqa: N802
                p = QPainter(self)
                paint_keycap(p, self.width(), self.height() / 2, text)
                p.end()

        w = _Cap()
        w.setFixedSize(int(keycap_width(text)) + 1, 24)
        w.setFocusPolicy(Qt.NoFocus)
        return w

    @property
    def _s(self) -> CameraSession | None:
        return self._session

    def bind(self, session: CameraSession | None) -> None:
        self._session = session
        self.setEnabled(session is not None)

    def _focus(self, v: float) -> None:
        self._focus_moving = v != 0.0
        if self._s:
            self._s.set_focus_velocity(v)

    def set_stick_size(self, px: int) -> None:
        """Стик и качелька зума крупнее, когда в колонке есть место (высокий экран); STICK_MIN — окно 1640×850."""
        px = max(STICK_MIN, min(STICK_MAX, px))
        if px != self.joystick.width():
            self.joystick.setFixedSize(px, px)
            self.zoom_slider.setFixedHeight(ZOOM_ROCKER_H + px - STICK_MIN)

    def release_all(self) -> None:
        """Отпустить всё, что может двигать камеру: джойстик, зум, фокус."""
        self.joystick.release()
        if self.zoom_slider.value() != 0:
            self.zoom_slider.setValue(0)
        if self.focus_slider.value() != 0:
            self.focus_slider.setValue(0)
        if self._focus_moving:
            for b in (self.near_btn, self.far_btn):
                b.setDown(False)
            self._focus(0.0)

    def set_sensitivity(self, sens: int, slow_on_tele: bool) -> None:
        """Показать сохранённые настройки, не отправляя сигналов."""
        self.sensitivity = clamp_sens(sens)
        for wd, val in ((self.sens_slider, self.sensitivity), (self.slow_tele_check, bool(slow_on_tele))):
            wd.blockSignals(True)
            (wd.setValue if wd is self.sens_slider else wd.setChecked)(val)
            wd.blockSignals(False)

    def _on_sens(self, v: int) -> None:
        self.sensitivity = clamp_sens(v)
        self.sensitivityChanged.emit(self.sensitivity)

    def _on_joystick(self, x: float, y: float) -> None:
        if self._s:
            self._s.set_pan_tilt_velocity(shape(x, self.sensitivity), shape(y, self.sensitivity))

    def _on_zoom(self, v: int) -> None:
        if self._s:
            self._s.set_zoom_velocity(v / 100)

    def _on_iris_auto(self, on: bool) -> None:
        self._touch("iris")
        self.iris_slider.setEnabled(not on)
        if self._s:
            self._s.set_iris_auto(on)

    def _on_iris(self, v: int) -> None:
        self._touch("iris")
        if self._s:
            self._s.set_iris(v / 100)

    # --- изображение: gain и баланс белого --------------------------------

    def _touch(self, name: str) -> None:
        self._touched[name] = time.monotonic()

    def _show_values(self) -> None:
        code = P.GAIN_CODES[self.gain_slider.value()]
        self.gain_value.setText("авто" if self.gain_auto.isChecked() else f"{P.gain_db(code)} dB")
        self.gain_slider.setEnabled(not self.gain_auto.isChecked())
        var = self.wb_combo.currentData() == P.WB_VAR
        self.ctemp_slider.setEnabled(var)
        self.ctemp_value.setText(f"≈{P.color_temp_kelvin(self.ctemp_slider.value())} K" if var else "")

    def _on_gain_auto(self, on: bool) -> None:
        self._touch("gain")
        self._show_values()
        if self._s:
            self._s.set_gain(P.GAIN_AUTO if on else P.GAIN_CODES[self.gain_slider.value()])

    def _on_gain(self, i: int) -> None:
        self._touch("gain")
        self._show_values()
        if self._s and not self.gain_auto.isChecked():
            self._s.set_gain(P.GAIN_CODES[i])

    def _on_wb(self, _i: int) -> None:
        self._touch("wb")
        self._show_values()
        if self._s:
            self._s.set_wb(self.wb_combo.currentData())

    def _on_ctemp(self, v: int) -> None:
        self._touch("ctemp")
        self._show_values()
        if self._s and self.wb_combo.currentData() == P.WB_VAR:
            self._s.set_color_temp(v)

    def set_unsupported(self, features) -> None:
        """Спрятать то, что камера не умеет (ответила ER1): gain, баланс белого, цветовая температура.
        Пустой список показывает всё (сменили камеру)."""
        feats = set(features)
        self._set_row_visible(self._gain_row, "gain" not in feats)
        no_wb = "wb" in feats
        self._set_row_visible(self._wb_row, not no_wb)
        if not no_wb:  # без режима WB прячется вся строка; иначе — только ползунок температуры
            self.ctemp_slider.setVisible("color_temp" not in feats)
            self.ctemp_value.setVisible("color_temp" not in feats)

    @staticmethod
    def _set_row_visible(row: QHBoxLayout, visible: bool) -> None:
        for i in range(row.count()):
            if w := row.itemAt(i).widget():
                w.setVisible(visible)

    def sync_image(self, img: ImageState) -> None:
        """Показать значения камеры, не отправляя команд; не перебивать то, что оператор трогал в последние 1,5 с."""
        now = time.monotonic()

        def fresh(name: str) -> bool:
            return now - self._touched.get(name, float("-inf")) > TOUCH_HOLD_S

        widgets = (self.gain_auto, self.gain_slider, self.wb_combo, self.ctemp_slider, self.iris_auto, self.iris_slider)
        for wd in widgets:
            wd.blockSignals(True)
        try:
            if img.gain is not None and fresh("gain"):
                self.gain_auto.setChecked(img.gain == P.GAIN_AUTO)
                if img.gain in P.GAIN_CODES:
                    self.gain_slider.setValue(P.GAIN_CODES.index(img.gain))
            if img.wb is not None and fresh("wb"):
                i = self.wb_combo.findData(img.wb)
                if i >= 0:
                    self.wb_combo.setCurrentIndex(i)
            if img.color_temp is not None and fresh("ctemp"):
                self.ctemp_slider.setValue(img.color_temp)
            # ирис: «Авто» снимает и крестовина геймпада (CameraSession.iris_step) — флажок по ответам камеры
            if fresh("iris"):
                if img.iris_auto is not None:
                    self.iris_auto.setChecked(img.iris_auto)
                    self.iris_slider.setEnabled(not img.iris_auto)
                if img.iris is not None:
                    self.iris_slider.setValue(round(100 * (img.iris - P.LENS_MIN) / (P.LENS_MAX - P.LENS_MIN)))
        finally:
            for wd in widgets:
                wd.blockSignals(False)
        self._show_values()

    def _preset_clicked(self, n: int) -> None:
        if not self._s:
            return
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            ans = QMessageBox.question(self, "Пресет", f"Записать текущее положение в пресет {n}?")
            if ans == QMessageBox.Yes:
                self._s.store_preset(n - 1)
        else:
            self._s.recall_preset(n - 1)
