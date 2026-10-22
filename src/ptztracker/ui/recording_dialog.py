from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ptztracker.config import RECORD_BUFFER_MAX_S, RECORD_BUFFER_MIN_S, AppConfig, record_dir


class RecordingDialog(QDialog):
    """Настройки чёрного ящика. Меняет cfg только по apply() (кнопка OK)."""

    def __init__(self, cfg: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки записи")
        self._cfg = cfg
        self.enabled = QCheckBox("Вести запись (чёрный ящик)")
        self.enabled.setChecked(cfg.record_enabled)
        self.minutes = QSpinBox()
        self.minutes.setRange(RECORD_BUFFER_MIN_S // 60, RECORD_BUFFER_MAX_S // 60)
        self.minutes.setSuffix(" мин")
        self.minutes.setValue(cfg.record_buffer_s // 60)
        self.to_disk = QCheckBox("Писать всё на диск (около 450 МБ в час)")
        self.to_disk.setChecked(cfg.record_to_disk)
        self._initial_folder = str(record_dir(cfg))
        self.folder = QLineEdit(self._initial_folder)
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._browse)

        lay = QVBoxLayout(self)
        lay.addWidget(self.enabled)
        row = QHBoxLayout()
        row.addWidget(QLabel("Сколько последних минут хранить"))
        row.addWidget(self.minutes)
        lay.addLayout(row)
        lay.addWidget(self.to_disk)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Папка"))
        frow.addWidget(self.folder, 1)
        frow.addWidget(browse)
        lay.addLayout(frow)
        note = QLabel("Изменения вступят в силу при следующем выборе камеры или перезапуске программы.")
        note.setWordWrap(True)
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Папка для записей", self.folder.text())
        if d:
            self.folder.setText(d)

    def apply(self) -> None:
        c = self._cfg
        c.record_enabled = self.enabled.isChecked()
        c.record_buffer_s = self.minutes.value() * 60
        c.record_to_disk = self.to_disk.isChecked()
        text = self.folder.text().strip()
        # не трогали папку — остаётся то, что было (пусто = «Записи PTZ Tracker» на рабочем столе)
        if text != self._initial_folder:
            c.record_dir = text
