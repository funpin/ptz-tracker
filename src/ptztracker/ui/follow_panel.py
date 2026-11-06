from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ptztracker.control.composition import COMPOSITIONS
from ptztracker.ui.hints import HintBar, hint_for
from ptztracker.ui.state import TrackingView, tracking_view
from ptztracker.ui.theme import FS_CAPTION, SP, C, qcolor
from ptztracker.ui.widgets import SectionHeader, SilhouetteButton, TrackButton

# крупность кадра: три силуэта и ползунок на трёх отметках (спека 2026-11-05, «Кадр»). Пояс слева/справа
# убран из кнопок — вернётся перетаскиванием зоны в 1.2; агент и старые настройки ещё могут его задать
SIZES = ("waist_center", "full", "wide")
SIZE_TITLES = {"waist_center": "Пояс", "full": "Рост", "wide": "Общий"}
SIZE_KEYS = ("Z", "X", "C")  # цифры 1–9 — пресеты (план шага Б, часть 3)


class MarkedSlider(QSlider):
    """Ползунок крупности с засечками под каждой отметкой (QSS-ползунок сам засечки не рисует)."""

    HANDLE = 16

    def mark_xs(self) -> list[float]:
        n = self.maximum() - self.minimum()
        span = self.width() - self.HANDLE
        return [self.HANDLE / 2 + span * i / n for i in range(n + 1)] if n > 0 else []

    def __init__(self, *a):
        super().__init__(*a)
        self.setMinimumHeight(24)

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        p = QPainter(self)  # засечки — под дорожкой, ручка их не закрывает
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(qcolor(C.GREY), 1.5))
        y = self.height() / 2 + 8
        for x in self.mark_xs():
            p.drawLine(QPointF(x, y), QPointF(x, y + 3))
        p.end()


class FollowPanel(QWidget):
    """Композиция кадра и характер движения. Кнопку TRACK (follow_btn) и пояснение (status_label)
    окно ставит в верхнюю панель и поверх видео."""

    followRequested = Signal(bool)  # оператор нажал плитку: True — включить
    compositionChanged = Signal(str)
    effortChanged = Signal(float)  # 0..1, применяется сразу при любом изменении
    calibrateRequested = Signal()  # кнопка «Калибровка…» в плитке при раскачке

    def __init__(self, parent=None):
        super().__init__(parent)
        self.follow_btn = TrackButton()
        self.follow_btn.clicked.connect(lambda checked: self.followRequested.emit(checked))
        self.follow_btn.actionClicked.connect(self.calibrateRequested)
        self.status_label = HintBar()  # подсказка управления поверх видео (окно ставит её внизу слева)
        self._view: TrackingView | None = None
        self._ctx = dict(has_target=False, target_id=None, pad=False, zone_hover=False)

        self.comp_group = QButtonGroup(self)
        self.comp_group.setExclusive(True)
        self.comp_buttons: dict[str, SilhouetteButton] = {}
        grid = QHBoxLayout()
        grid.setSpacing(0)
        for i, key in enumerate(SIZES):
            b = SilhouetteButton(key, f"{SIZE_TITLES[key]} · {SIZE_KEYS[i]}")
            b.setToolTip(f"{COMPOSITIONS[key].title} — клавиша {SIZE_KEYS[i]}")
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            self.comp_group.addButton(b)
            self.comp_buttons[key] = b
            if i:
                grid.addStretch(1)
            grid.addWidget(b)
        self.size_slider = MarkedSlider(Qt.Horizontal)
        self.size_slider.setRange(0, len(SIZES) - 1)
        self.size_slider.setPageStep(1)
        self.size_slider.setFocusPolicy(Qt.NoFocus)
        self.size_slider.setToolTip("Крупность: пояс → рост → общий (плавные промежуточные — в версии 1.2)")
        self.size_slider.valueChanged.connect(lambda v: self._pick(SIZES[v]))

        self.effort_slider = QSlider(Qt.Horizontal)
        self.effort_slider.setRange(0, 100)
        self.effort_slider.setFocusPolicy(Qt.NoFocus)
        # любое изменение (перетаскивание, клик по полосе, колесо) применяется сразу —
        # иначе ползунок показывал бы не то, что действует
        self.effort_slider.valueChanged.connect(lambda v: self.effortChanged.emit(v / 100))
        erow = QHBoxLayout()
        erow.setSpacing(SP[1])
        erow.addWidget(QLabel("Мягко"))
        erow.addWidget(self.effort_slider, 1)
        erow.addWidget(QLabel("Резко"))

        self._calibrated, self._note = True, None
        self.calib_label = QLabel("")
        self.calib_label.setWordWrap(True)
        self.calib_label.linkActivated.connect(lambda _h: self.calibrateRequested.emit())  # «Откалибровать»
        self.calib_label.setStyleSheet(f"color: {C.LOST}; font-size: {FS_CAPTION}px;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP[1])
        self.title_header = SectionHeader("Кадр")  # окно прячет: над колонкой — имя выбранной камеры
        lay.addWidget(self.title_header)
        lay.addLayout(grid)
        lay.addWidget(self.size_slider)
        lay.addLayout(erow)
        lay.addWidget(self.calib_label)
        self.set_state(False, None)

    def set_view(self, v: TrackingView) -> None:
        self.follow_btn.set_view(v.kind, v.word, v.why, v.show_calibrate)
        self.follow_btn.setChecked(v.kind in ("on", "lost"))
        self._view = v
        self._show_hint_bar()

    def set_hint_context(self, has_target: bool, target_id: int | None, pad: bool, zone_hover: bool) -> None:
        """Что показать в подсказке: есть ли цель, управляет ли камерой геймпад, навели ли на зону."""
        ctx = dict(has_target=has_target, target_id=target_id, pad=pad, zone_hover=zone_hover)
        if ctx != self._ctx:
            self._ctx = ctx
            self._show_hint_bar()

    def _show_hint_bar(self) -> None:
        if self._view is not None:
            self.status_label.set_hint(*hint_for(self._view.kind, self._view.why, **self._ctx))

    def set_state(self, enabled: bool, reason: str | None) -> None:
        self.set_view(tracking_view(enabled, reason, None, None))

    def show_hint(self, text: str) -> None:
        self.follow_btn.set_why(text)
        self.status_label.setText(text)

    def _pick(self, key: str) -> None:
        self._show_size(key)
        self.compositionChanged.emit(key)

    def _show_size(self, key: str) -> None:
        size = key if key in SIZES else "waist_center"  # пояс слева/справа — пока тоже «Пояс»
        self.comp_buttons[size].setChecked(True)
        self.size_slider.blockSignals(True)
        self.size_slider.setValue(SIZES.index(size))
        self.size_slider.blockSignals(False)

    def set_composition(self, key: str) -> None:
        """Показать крупность (без команды): из настроек камеры, агента или геймпада."""
        self._show_size(key)

    def set_effort(self, x: float) -> None:
        self.effort_slider.blockSignals(True)
        self.effort_slider.setValue(round(x * 100))
        self.effort_slider.blockSignals(False)

    def set_calibrated(self, calibrated: bool) -> None:
        self._calibrated = calibrated
        self._note = None  # подсказка «откалибруйте заново» относится к одной калибровке
        self._show_calib_label()

    def set_calibration_note(self, note: str | None) -> None:
        """Мягкая подсказка при уже сделанной калибровке (например, «сделана на другой модели»); None — убрать."""
        self._note = note
        self._show_calib_label()

    def _show_calib_label(self) -> None:
        if not self._calibrated:
            text = (f"Камера не откалибрована — <a href='calibrate' style='color: {C.LOST};'>"
                    "откалибровать</a> (2 мин)")
            color = C.LOST  # без калибровки слежение хуже: предупреждение
        else:
            text = self._note or ""
            color = C.TEXT_DIM  # «откалибруйте заново» — спокойная подсказка, не тревога
        self.calib_label.setStyleSheet(f"color: {color}; font-size: {FS_CAPTION}px;")
        self.calib_label.setText(text)
        self.calib_label.setVisible(bool(text))
