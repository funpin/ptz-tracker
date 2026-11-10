from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ptztracker.config import CameraConfig, is_valid_ip
from ptztracker.ui.hub_view import hub_source_name
from ptztracker.ui.theme import FS_CAPTION, R_PANEL, SP, C
from ptztracker.ui.widgets import ToggleCheck
from ptztracker.videohub.client import HubSnapshot
from ptztracker.videohub.protocol import MAX_PORTS, hub_input_of

DOTS = {"ok": C.LIME, "fault": C.FAULT}
REFRESH_MS = 500
OFFLINE_PORTS = 40  # без связи число портов неизвестно: номера 1…40 (и текущий, если больше)
LABEL_W = 110


class VideohubDialog(QDialog):
    """Окно «Videohub» по макету docs/design/mockups/videohub.html: адрес, связь, выход хаба, переключатель
    и для каждой камеры сетки — её вход хаба. Списки подписаны названиями, которые сообщает сам хаб."""

    def __init__(self, ip: str, output: int, follow: bool, cameras: list[tuple[int, CameraConfig]],
                 parent=None, snapshot: Callable[[], HubSnapshot] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Videohub — выход вслед за камерой")
        self._initial = ip.strip()
        self._snapshot = snapshot
        self._cameras = cameras
        self._ports_key = None
        intro = QLabel("Когда в программе выбирается другая камера, хаб подаёт на выбранный выход её вход. "
                       f"Камерами это <b style='color:{C.TEXT}'>не управляет</b>. Хаб должен быть в той же сети.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.ip_edit = QLineEdit(ip)
        self.ip_edit.setPlaceholderText("например, 10.10.10.50")
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(8, 8)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {C.FAULT}; font-size: {FS_CAPTION}px;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        st = QHBoxLayout()
        st.setContentsMargins(LABEL_W + SP[1], 0, 0, 0)
        st.setSpacing(SP[1])
        st.addWidget(self.status_dot)
        st.addWidget(self.status_label, 1)
        self.output_combo = QComboBox()
        self._output = output
        self.follow_check = ToggleCheck("Переключать выход вслед за камерой")
        self.follow_check.setChecked(follow)
        fl = QHBoxLayout()
        fl.setContentsMargins(LABEL_W + SP[1], 0, 0, 0)
        fl.addWidget(self.follow_check)
        fl.addStretch(1)

        self.cams_title = QLabel("КАМЕРЫ И ВХОДЫ ХАБА")
        self.cams_title.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: 11px; font-weight: 600;"
                                      " letter-spacing: 1px;")
        self.cams_panel = QFrame()
        self.cams_panel.setObjectName("camsPanel")
        self.cams_panel.setStyleSheet(f"QFrame#camsPanel {{ background: {C.SURFACE}; border: 1px solid {C.LINE};"
                                      f" border-radius: {R_PANEL}px; }} QLabel {{ background: transparent; }}")
        cl = QVBoxLayout(self.cams_panel)
        cl.setContentsMargins(1, 1, 1, 1)
        cl.setSpacing(0)
        self.input_combos: list[QComboBox] = []
        self._inputs = [hub_input_of(cam) for _slot, cam in cameras]
        for i, (slot, cam) in enumerate(cameras):
            row = QWidget()
            row.setObjectName("camRow")
            row.setStyleSheet("QWidget#camRow { background: %s; }"
                              % ("rgba(245,245,242,0.035)" if i % 2 else "transparent"))
            h = QHBoxLayout(row)
            h.setContentsMargins(SP[2], 6, SP[2], 6)
            h.setSpacing(SP[1])
            tile = QLabel(str(slot))
            tile.setFixedSize(18, 18)
            tile.setAlignment(Qt.AlignCenter)
            tile.setStyleSheet(f"background: {C.RAISED_HI}; color: {C.TEXT}; border-radius: 5px; font-size: 11px;"
                               " font-weight: 700;")
            h.addWidget(tile)
            h.addWidget(QLabel(cam.name), 1)
            combo = QComboBox()
            combo.setMinimumWidth(200)
            h.addWidget(combo)
            self.input_combos.append(combo)
            cl.addWidget(row)
        for w in (self.cams_title, self.cams_panel):
            w.setVisible(bool(cameras))
        self.now_label = QLabel("")
        self.now_label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")

        self.off_button = QPushButton("Выключить хаб")
        self.off_button.setProperty("variant", "ghost")
        self.off_button.setAutoDefault(False)
        self.off_button.setVisible(bool(self._initial))
        self.off_button.clicked.connect(self._turn_off)
        cancel = QPushButton("Отмена")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        self.ok_button = QPushButton("Сохранить")
        self.ok_button.setProperty("variant", "primary")
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.setSpacing(SP[2])
        foot.addWidget(self.off_button)
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(self.ok_button)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[3], SP[3], SP[3], SP[3])
        lay.setSpacing(SP[2])
        lay.addWidget(intro)
        lay.addLayout(self._row("Адрес хаба", self.ip_edit))
        lay.addLayout(st)
        lay.addWidget(self.error_label)
        lay.addLayout(self._row("Выход хаба", self.output_combo))
        lay.addLayout(fl)
        lay.addWidget(self.cams_title)
        lay.addWidget(self.cams_panel)
        lay.addWidget(self.now_label)
        lay.addLayout(foot)
        self.setMinimumWidth(540)
        self.ip_edit.textChanged.connect(self.error_label.hide)
        self.ip_edit.textChanged.connect(self._refresh)
        self.output_combo.currentIndexChanged.connect(self._refresh_now)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    @staticmethod
    def _row(text: str, w: QWidget) -> QHBoxLayout:
        label = QLabel(text)
        label.setStyleSheet(f"color: {C.TEXT_DIM};")
        label.setFixedWidth(LABEL_W)
        row = QHBoxLayout()
        row.addWidget(label)
        row.addWidget(w, 1)
        return row

    # --- состояние -----------------------------------------------------

    def _snap(self) -> HubSnapshot:
        """Снимок хаба — только если адрес в поле тот, к которому подключён клиент."""
        if self._snapshot is None or not self.ip() or self.ip() != self._initial:
            return HubSnapshot()
        return self._snapshot()

    def _set_status(self, text: str, kind: str) -> None:
        self.status_dot.setStyleSheet(f"background: {DOTS.get(kind, C.GREY)}; border-radius: 4px;")
        self.status_label.setText(text)

    def _refresh(self) -> None:
        ip = self.ip()
        snap = self._snap()
        if not ip:
            self._set_status("Хаб выключен", "off")
        elif ip != self._initial:
            self._set_status("Адрес изменён — связь проверю после «Сохранить»", "off")
        elif snap.online and snap.state.present:
            model = snap.state.model or "Videohub"
            size = f" {snap.state.inputs}×{snap.state.outputs}" if snap.state.inputs else ""
            if str(snap.state.inputs) in model:  # «Smart Videohub 20 x 20» — размер уже в названии
                size = ""
            self._set_status(f"На связи · {model}{size}", "ok")
        elif snap.online:
            self._set_status("Сервер отвечает, но хаб не подключён или ждёт обновления", "fault")
        elif self._snapshot is not None:
            self._set_status("Нет связи: проверьте адрес и что хаб в той же сети", "fault")
        else:
            self._set_status("Связь проверится после «Сохранить»", "off")
        self._refresh_ports(snap)
        self._refresh_now()

    def _refresh_ports(self, snap: HubSnapshot) -> None:
        """Списки выходов и входов: с подписями хаба при связи, номерами без неё. Выбор сохраняется."""
        live = snap.online and bool(snap.state.present)
        st = snap.state
        key = (live, st.inputs, st.outputs, tuple(sorted(st.input_labels.items())),
               tuple(sorted(st.output_labels.items()))) if live else (False,)
        if key == self._ports_key:
            return
        self._ports_key = key
        out = self.output()
        self._fill(self.output_combo, st.outputs if live and st.outputs else OFFLINE_PORTS,
                   st.output_labels if live else {}, out, none_text="не задан")
        for i, combo in enumerate(self.input_combos):
            cur = combo.currentData() if combo.count() else self._inputs[i]
            self._fill(combo, st.inputs if live and st.inputs else OFFLINE_PORTS,
                       st.input_labels if live else {}, cur, none_text="не переключать")

    @staticmethod
    def _fill(combo: QComboBox, n: int, labels: dict, current: int, none_text: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(none_text, 0)
        top = min(MAX_PORTS, max(n, current or 0))
        for k in range(1, top + 1):
            lab = labels.get(k, "")
            combo.addItem(f"{k} · {lab}" if lab else str(k), k)
        idx = combo.findData(current or 0)
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)

    def _refresh_now(self) -> None:
        out = self.output()
        snap = self._snap()
        if not out:
            self.now_label.setText("Выберите выход хаба")
        elif not snap.online:
            self.now_label.setText(f"Сейчас на выходе {out}: неизвестно")
        elif snap.state.locks.get(out) == "L":
            self.now_label.setText(f"Выход {out} заблокирован с другого пульта — программа его не переключает")
        else:
            src = hub_source_name(snap, out, [(cam.name, hub_input_of(cam)) for _s, cam in self._cameras])
            self.now_label.setText(f"Сейчас на выходе {out}: {src or 'неизвестно'}")

    # --- результат -----------------------------------------------------

    def ip(self) -> str:
        return self.ip_edit.text().strip()

    def output(self) -> int:
        d = self.output_combo.currentData()
        return d if isinstance(d, int) else self._output

    def values(self) -> tuple[str, int, bool]:
        return self.ip(), self.output(), self.follow_check.isChecked()

    def camera_inputs(self) -> list[tuple[CameraConfig, int]]:
        return [(cam, int(combo.currentData() or 0)) for (_s, cam), combo in zip(self._cameras, self.input_combos)]

    def _turn_off(self) -> None:
        self.ip_edit.clear()
        self.accept()

    def accept(self) -> None:
        ip = self.ip()
        if ip and not is_valid_ip(ip):
            self.error_label.setText("Это не похоже на адрес. Пример: 10.10.10.50")
            self.error_label.show()
            return
        self._timer.stop()
        super().accept()

    def reject(self) -> None:
        self._timer.stop()
        super().reject()
