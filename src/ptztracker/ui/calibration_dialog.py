from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout


class _Worker(QThread):
    progress = Signal(str)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, runner_factory):
        super().__init__()
        self.runner_factory = runner_factory

    def run(self):
        try:
            calib = self.runner_factory(self.progress.emit).run()
            self.done.emit(calib.to_json())
        except Exception as e:
            self.failed.emit(str(e))


class CalibrationDialog(QDialog):
    """Мастер калибровки: камера ~1–2 минуты поворачивается и зумирует, затем возвращается на место."""

    def __init__(self, runner_factory: Callable, on_saved: Callable[[dict], None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Калибровка камеры")
        self.resize(560, 440)
        self._factory = runner_factory
        self._on_saved = on_saved
        self._worker: _Worker | None = None
        intro = QLabel(
            "Программа измерит, как быстро эта камера поворачивается и зумирует на разных скоростях, "
            "чтобы слежение было плавным. Камера будет поворачиваться влево-вправо и зумировать "
            "1–2 минуты, затем вернётся на место. Наведите её на сцену с деталями (не на однотонную стену)."
        )
        intro.setWordWrap(True)
        self.off_air_check = QCheckBox("Камера НЕ в эфире — можно двигать")
        self.start_button = QPushButton("Начать калибровку")
        self.start_button.setDefault(True)
        self.start_button.setEnabled(False)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.close_button = QPushButton("Закрыть")
        row = QHBoxLayout()
        row.addWidget(self.start_button)
        row.addStretch(1)
        row.addWidget(self.close_button)
        lay = QVBoxLayout(self)
        for w in (intro, self.off_air_check, self.log_view, self.result_label):
            lay.addWidget(w)
        lay.addLayout(row)
        self.off_air_check.toggled.connect(self.start_button.setEnabled)
        self.start_button.clicked.connect(self._start)
        self.close_button.clicked.connect(self.reject)

    def _start(self) -> None:
        self.start_button.setEnabled(False)
        self.off_air_check.setEnabled(False)
        self.close_button.setEnabled(False)
        self.result_label.setText("")
        self._worker = _Worker(self._factory)
        self._worker.progress.connect(self.log_view.appendPlainText)
        self._worker.done.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _finish(self) -> None:
        self.close_button.setEnabled(True)
        self.off_air_check.setEnabled(True)
        self.start_button.setEnabled(self.off_air_check.isChecked())

    def _done(self, calib: dict) -> None:
        self._finish()
        self._on_saved(calib)
        self.result_label.setText("Готово: калибровка сохранена для этой камеры.")

    def _failed(self, err: str) -> None:
        self._finish()
        self.result_label.setText(f"Калибровка не удалась: {err}")

    def reject(self) -> None:
        if self._worker and self._worker.isRunning():
            return  # нельзя закрыть посреди замеров — камера в движении
        super().reject()
