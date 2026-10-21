from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ptztracker.diagnostics.runner import DiagContext, default_report_dir, run_diagnostics
from ptztracker.ui.theme import C


class _Worker(QThread):
    progress = Signal(str)
    ask = Signal(str, float)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, ctx_kwargs: dict):
        super().__init__()
        self.ctx_kwargs = ctx_kwargs

    def _ask_user(self, msg: str, seconds: float) -> None:
        self.ask.emit(msg, seconds)
        time.sleep(seconds)
        self.ask.emit("", 0.0)

    def run(self):
        try:
            ctx = DiagContext(ask_user=self._ask_user, progress=self.progress.emit, **self.ctx_kwargs)
            self.done.emit(str(run_diagnostics(ctx)))
        except Exception as e:  # отчёт должен получиться в любом случае; если нет — показать причину
            self.failed.emit(str(e))


class DiagnosticsDialog(QDialog):
    def __init__(
        self,
        host: str,
        username: str | None,
        password: str | None,
        out_dir: Path | None = None,
        notify_port: int = 31004,
        notify_wait_s: float = 15.0,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Диагностика камеры {host}")
        self.resize(640, 520)
        if out_dir is None:
            out_dir = default_report_dir()
        self._ctx_kwargs = dict(
            host=host,
            username=username,
            password=password,
            out_dir=Path(out_dir),
            notify_port=notify_port,
            notify_wait_s=notify_wait_s,
        )
        self.result_path: str | None = None
        self._worker: _Worker | None = None

        intro = QLabel(
            "Программа проверит связь, видеопоток и команды камеры и сохранит отчёт-архив "
            f"в папку:\n{out_dir}\n\nЭтот архив нужно прислать разработчику."
        )
        intro.setWordWrap(True)
        self.allow_move_check = QCheckBox("Разрешить движение камеры (камера НЕ должна быть в эфире)")
        self.start_button = QPushButton("Запустить")
        self.start_button.setDefault(True)
        self.prompt_label = QLabel("")
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        self.prompt_label.setFont(f)
        self.prompt_label.setWordWrap(True)
        self.prompt_label.setStyleSheet(f"color: {C.LOST};")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.open_button = QPushButton("Открыть папку с отчётом")
        self.open_button.setEnabled(False)
        self.close_button = QPushButton("Закрыть")

        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addStretch(1)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.close_button)
        lay = QVBoxLayout(self)
        for w in (intro, self.allow_move_check, self.prompt_label, self.log_view, self.result_label):
            lay.addWidget(w)
        lay.addLayout(buttons)

        self._countdown_end = 0.0
        self._prompt_text = ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self.start_button.clicked.connect(self._start)
        self.open_button.clicked.connect(self._open_folder)
        self.close_button.clicked.connect(self.reject)

    def _start(self) -> None:
        self.start_button.setEnabled(False)
        self.allow_move_check.setEnabled(False)
        self.close_button.setEnabled(False)
        self.log_view.clear()
        self._worker = _Worker({**self._ctx_kwargs, "allow_move": self.allow_move_check.isChecked()})
        self._worker.progress.connect(self.log_view.appendPlainText)
        self._worker.ask.connect(self._on_ask)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_ask(self, msg: str, seconds: float) -> None:
        self._prompt_text = msg
        self._countdown_end = time.monotonic() + seconds
        if msg:
            self._timer.start(200)
            self._tick()
        else:
            self._timer.stop()
            self.prompt_label.setText("")

    def _tick(self) -> None:
        left = max(0, self._countdown_end - time.monotonic())
        self.prompt_label.setText(f"{self._prompt_text}\nОсталось {left:.0f} с")

    def _finish(self) -> None:
        self.close_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.allow_move_check.setEnabled(True)
        self._timer.stop()
        self.prompt_label.setText("")

    def _on_done(self, path: str) -> None:
        self._finish()
        self.result_path = path
        self.result_label.setText(f"Готово. Отчёт: {path}")
        self.open_button.setEnabled(True)

    def _on_failed(self, err: str) -> None:
        self._finish()
        self.result_label.setText(f"Диагностика прервалась: {err}")

    def _open_folder(self) -> None:
        if self.result_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.result_path).parent)))

    def reject(self) -> None:
        if self._worker and self._worker.isRunning():
            return  # нельзя закрыть посреди проверки — камера может быть в движении
        super().reject()
