from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ptztracker.config import is_valid_ip
from ptztracker.ui.theme import FS_CAPTION, R_PANEL, R_TAG, SP, C

Status = Callable[[], "tuple[str, str]"]  # («на связи», ok | fault | off)
Cameras = Callable[[], "list[tuple[int, str, int, str | None]]"]  # (клетка 1–4, имя, вход, pgm | pvw | None)
DOTS = {"ok": C.LIME, "fault": C.FAULT}
REFRESH_MS = 500


class AtemDialog(QDialog):
    """Адрес видеомикшера ATEM для Tally по макету docs/design/mockups/pads-atem.html: что это, связь, какая
    камера на каком входе и что сейчас в эфире. Пусто («Выключить Tally») — Tally выключен."""

    def __init__(self, ip: str = "", parent=None, status: Status | None = None, cameras: Cameras | None = None):
        super().__init__(parent)
        self.setWindowTitle("ATEM — эфирные огни (Tally)")
        self._initial = ip.strip()
        self._status = status
        self._cameras = cameras
        intro = QLabel("Программа узнаёт у видеомикшера ATEM, какая камера в эфире, и показывает это рамкой "
                       f"клетки. Камерой это <b style='color:{C.TEXT}'>не управляет</b>. "
                       "ATEM должен быть в той же сети.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.ip_edit = QLineEdit(ip)
        self.ip_edit.setPlaceholderText("например, 192.168.10.240")
        label = QLabel("Адрес ATEM")
        label.setStyleSheet(f"color: {C.TEXT_DIM};")
        label.setFixedWidth(110)
        ip_row = QHBoxLayout()
        ip_row.addWidget(label)
        ip_row.addWidget(self.ip_edit, 1)
        # связь — строкой под адресом: лайм — на связи, красный — нет, серый — не настроен или ещё не проверен
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(8, 8)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {C.FAULT}; font-size: {FS_CAPTION}px;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        st = QHBoxLayout()
        st.setContentsMargins(110 + SP[1], 0, 0, 0)
        st.setSpacing(SP[1])
        st.addWidget(self.status_dot)
        st.addWidget(self.status_label, 1)
        # камеры и их входы: плитка номера цветом эфира, вход, PGM/PVW
        self.cams_title = QLabel("КАМЕРЫ И ВХОДЫ")
        self.cams_title.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: 11px; font-weight: 600;"
                                      " letter-spacing: 1px;")
        self.cams_panel = QFrame()
        self.cams_panel.setObjectName("camsPanel")
        self.cams_panel.setStyleSheet(f"QFrame#camsPanel {{ background: {C.SURFACE}; border: 1px solid {C.LINE};"
                                      f" border-radius: {R_PANEL}px; }} QLabel {{ background: transparent; }}")
        self._cams_lay = QVBoxLayout(self.cams_panel)
        self._cams_lay.setContentsMargins(1, 1, 1, 1)
        self._cams_lay.setSpacing(0)
        self.camera_labels: list[QLabel] = []
        self._cams_key = None
        self.cams_note = QLabel("Вход камеры задаётся в её окне: ⋯ в шапке клетки → Изменить камеру.")
        self.cams_note.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")

        self.off_button = QPushButton("Выключить Tally")
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
        lay.addLayout(ip_row)
        lay.addLayout(st)
        lay.addWidget(self.error_label)
        lay.addWidget(self.cams_title)
        lay.addWidget(self.cams_panel)
        lay.addWidget(self.cams_note)
        lay.addLayout(foot)
        self.setMinimumWidth(500)
        self.ip_edit.textChanged.connect(self.error_label.hide)
        self.ip_edit.textChanged.connect(self._refresh)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _set_status(self, text: str, kind: str) -> None:
        self.status_dot.setStyleSheet(f"background: {DOTS.get(kind, C.GREY)}; border-radius: 4px;")
        self.status_label.setText(text)

    def _refresh(self) -> None:
        ip = self.ip()
        if not ip:
            self._set_status("Tally выключен", "off")
        elif ip != self._initial:
            self._set_status("Адрес изменён — связь проверю после «Сохранить»", "off")
        elif self._status is not None:
            text, kind = self._status()
            self._set_status(text[:1].upper() + text[1:], kind)
        else:
            self._set_status("Связь проверится после «Сохранить»", "off")
        self._refresh_cameras()

    def _refresh_cameras(self) -> None:
        cams = self._cameras() if self._cameras else []
        key = tuple(cams)
        if key == self._cams_key:
            return
        self._cams_key = key
        while self._cams_lay.count():
            w = self._cams_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.camera_labels = []
        for i, (slot, name, inp, tally) in enumerate(cams):
            row = QWidget()
            row.setObjectName("camRow")
            row.setStyleSheet("QWidget#camRow { background: %s; }"
                              % ("rgba(245,245,242,0.035)" if i % 2 else "transparent"))
            h = QHBoxLayout(row)
            h.setContentsMargins(SP[2], 6, SP[2], 6)
            h.setSpacing(SP[1])
            bg, fg = {"pgm": (C.PGM, C.TEXT), "pvw": (C.PVW, C.BG)}.get(tally or "", (C.RAISED_HI, C.TEXT))
            tile = QLabel(str(slot))
            tile.setFixedSize(16, 16)
            tile.setAlignment(Qt.AlignCenter)
            tile.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 4px; font-size: 10px;"
                               " font-weight: 700;")
            h.addWidget(tile)
            h.addWidget(QLabel(name), 1)
            inp_label = QLabel(f"вход {inp}" if inp else "вход не задан")
            inp_label.setStyleSheet(f"color: {C.TEXT_DIM if inp else C.GREY};")
            h.addWidget(inp_label)
            self.camera_labels.append(inp_label)
            if tally in ("pgm", "pvw"):
                chip = QLabel(tally.upper())
                chip.setStyleSheet(
                    f"background: {C.PGM}; color: {C.TEXT}; border-radius: {R_TAG}px; padding: 1px 6px;"
                    " font-weight: 700; font-size: 11px;" if tally == "pgm" else
                    f"color: {C.PVW}; border: 1px solid {C.PVW}; border-radius: {R_TAG}px; padding: 0 5px;"
                    " font-size: 11px;")
                h.addWidget(chip)
            self._cams_lay.addWidget(row)
        for w in (self.cams_title, self.cams_panel, self.cams_note):
            w.setVisible(bool(cams))

    def _turn_off(self) -> None:
        self.ip_edit.clear()
        self.accept()

    def ip(self) -> str:
        return self.ip_edit.text().strip()

    def accept(self) -> None:
        ip = self.ip()
        if ip and not is_valid_ip(ip):
            self.error_label.setText("Это не похоже на адрес. Пример: 192.168.10.240")
            self.error_label.show()
            return
        self._timer.stop()
        super().accept()

    def reject(self) -> None:
        self._timer.stop()
        super().reject()
