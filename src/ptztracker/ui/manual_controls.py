from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ptztracker.camera.session import CameraSession
from ptztracker.ui.joystick import JoystickWidget


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
    """Ручное управление: джойстик, зум, фокус, ирис, пресеты камеры."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session: CameraSession | None = None

        self.joystick = JoystickWidget()
        self.joystick.moved.connect(self._on_joystick)

        self.zoom_slider = SpringSlider(Qt.Horizontal)
        self.zoom_slider.setRange(-100, 100)
        self.zoom_slider.setToolTip("Тяните вправо — приблизить, влево — отдалить. Отпустите — стоп.")
        self.zoom_slider.valueChanged.connect(self._on_zoom)
        self.zoom_slider.sliderReleased.connect(lambda: self.zoom_slider.setValue(0))
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Шире"))
        zoom_row.addWidget(self.zoom_slider, 1)
        zoom_row.addWidget(QLabel("Ближе"))

        move_box = QGroupBox("Поворот и зум")
        mv = QVBoxLayout(move_box)
        mv.addWidget(self.joystick, 0, Qt.AlignHCenter)
        mv.addLayout(zoom_row)

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
        focus_box = QGroupBox("Фокус")
        fl = QGridLayout(focus_box)
        fl.addWidget(self.af_check, 0, 0)
        fl.addWidget(self.push_af, 0, 1)
        fl.addWidget(self.near_btn, 1, 0)
        fl.addWidget(self.far_btn, 1, 1)

        self.iris_auto = QCheckBox("Авто")
        self.iris_auto.setChecked(True)
        self.iris_slider = QSlider(Qt.Horizontal)
        self.iris_slider.setRange(0, 100)
        self.iris_slider.setValue(50)
        self.iris_slider.setEnabled(False)
        self.iris_auto.toggled.connect(self._on_iris_auto)
        self.iris_slider.valueChanged.connect(lambda v: self._s and self._s.set_iris(v / 100))
        iris_box = QGroupBox("Ирис / яркость")
        il = QHBoxLayout(iris_box)
        il.addWidget(self.iris_auto)
        il.addWidget(self.iris_slider, 1)

        preset_box = QGroupBox("Пресеты камеры (Shift+клик — записать)")
        pl = QGridLayout(preset_box)
        self.preset_buttons: list[QPushButton] = []
        for i in range(9):
            b = QPushButton(str(i + 1))
            b.clicked.connect(lambda _=False, n=i + 1: self._preset_clicked(n))
            pl.addWidget(b, i // 3, i % 3)
            self.preset_buttons.append(b)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for w in (move_box, focus_box, iris_box, preset_box):
            lay.addWidget(w)
        self.bind(None)

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

    def _preset_clicked(self, n: int) -> None:
        if not self._s:
            return
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            ans = QMessageBox.question(self, "Пресет", f"Записать текущее положение в пресет {n}?")
            if ans == QMessageBox.Yes:
                self._s.store_preset(n - 1)
        else:
            self._s.recall_preset(n - 1)
