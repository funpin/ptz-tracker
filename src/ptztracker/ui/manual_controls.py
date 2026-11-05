from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
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
from ptztracker.ui.joystick import JoystickWidget
from ptztracker.ui.theme import FS_CAPTION, H_STOP, SP, C
from ptztracker.ui.widgets import KeyButton, SectionHeader, keycap_width, paint_keycap

PRESET_SIZE = 32
ZOOM_ROCKER_H = 112  # вместе с клавишами и подписями — по высоте стика
TOUCH_HOLD_S = 1.5  # столько опрос камеры не перебивает ползунок, который оператор только что тронул


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
        zoom_col = QVBoxLayout()
        zoom_col.setSpacing(SP[0])
        for wd in (self._keycap_label("+"), self._caption("Ближе")):
            zoom_col.addWidget(wd, 0, Qt.AlignHCenter)
        zoom_col.addWidget(self.zoom_slider, 0, Qt.AlignHCenter)
        for wd in (self._caption("Шире"), self._keycap_label("−")):
            zoom_col.addWidget(wd, 0, Qt.AlignHCenter)

        stick_row = QHBoxLayout()
        stick_row.addStretch(1)
        stick_row.addWidget(self.joystick, 0, Qt.AlignVCenter)
        stick_row.addSpacing(SP[3] * 2)
        stick_row.addLayout(zoom_col)
        stick_row.addStretch(1)

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
        fl = QHBoxLayout()
        fl.setSpacing(SP[0])
        self.af_check.setText("AF")
        self.af_check.setToolTip("Автофокус")
        self.push_af.setText("Разовый")
        self.push_af.setToolTip("Разовый автофокус")
        for wd in (self.af_check, self.push_af, self.near_btn, self.far_btn):
            wd.setSizePolicy(QSizePolicy.Ignored, wd.sizePolicy().verticalPolicy())  # ряд — по ширине панели
            fl.addWidget(wd)

        self.iris_auto = QCheckBox("Авто")
        self.iris_auto.setChecked(True)
        self.iris_slider = QSlider(Qt.Horizontal)
        self.iris_slider.setRange(0, 100)
        self.iris_slider.setValue(50)
        self.iris_slider.setEnabled(False)
        self.iris_auto.toggled.connect(self._on_iris_auto)
        self.iris_slider.valueChanged.connect(lambda v: self._s and self._s.set_iris(v / 100))
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

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP[1])
        lay.addWidget(SectionHeader("Камера", "движение выключает слежение"))
        lay.addLayout(stick_row)
        lay.addWidget(self.stop_btn)
        lay.addLayout(il)
        lay.addLayout(gl)
        lay.addWidget(SectionHeader("Пресеты", "Shift+клик — записать"))
        lay.addLayout(pl)
        lay.addWidget(SectionHeader("Фокус и баланс белого"))
        lay.addLayout(fl)
        lay.addLayout(wl)
        self.bind(None)
        self._show_values()

    @staticmethod
    def _row(title: str, *widgets: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SP[1])
        lab = QLabel(title)
        lab.setMinimumWidth(36)
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

    def release_all(self) -> None:
        """Отпустить всё, что может двигать камеру: джойстик, зум, фокус."""
        self.joystick.release()
        if self.zoom_slider.value() != 0:
            self.zoom_slider.setValue(0)
        if self._focus_moving:
            for b in (self.near_btn, self.far_btn):
                b.setDown(False)
            self._focus(0.0)

    def _on_joystick(self, x: float, y: float) -> None:
        if self._s:
            self._s.set_pan_tilt_velocity(x, y)

    def _on_zoom(self, v: int) -> None:
        if self._s:
            self._s.set_zoom_velocity(v / 100)

    def _on_iris_auto(self, on: bool) -> None:
        self.iris_slider.setEnabled(not on)
        if self._s:
            self._s.set_iris_auto(on)

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

        widgets = (self.gain_auto, self.gain_slider, self.wb_combo, self.ctemp_slider)
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
