from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from ptztracker.config import is_valid_ip
from ptztracker.ui.theme import C


class AtemDialog(QDialog):
    """Адрес видеомикшера ATEM для Tally. Пусто — Tally выключен."""

    def __init__(self, ip: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("ATEM")
        self.ip_edit = QLineEdit(ip)
        self.ip_edit.setPlaceholderText("например, 192.168.10.240")
        hint = QLabel("ATEM должен быть в той же сети. Пусто — Tally выключен.")
        hint.setWordWrap(True)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {C.FAULT};")
        self.error_label.setWordWrap(True)
        self.error_label.hide()

        form = QFormLayout()
        form.addRow("Адрес ATEM", self.ip_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(hint)
        lay.addWidget(self.error_label)
        lay.addWidget(buttons)
        self.ip_edit.textChanged.connect(self.error_label.hide)

    def ip(self) -> str:
        return self.ip_edit.text().strip()

    def accept(self) -> None:
        ip = self.ip()
        if ip and not is_valid_ip(ip):
            self.error_label.setText("Это не похоже на адрес. Пример: 192.168.10.240")
            self.error_label.show()
            return
        super().accept()
