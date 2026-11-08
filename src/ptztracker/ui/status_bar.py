"""Строка состояния со своими сообщениями: родной showMessage в Qt прячет виджеты слева, а значок записи должен быть виден."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QSizePolicy, QStatusBar

from ptztracker.ui.theme import FS_CAPTION, C


class MessageStatusBar(QStatusBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.message_label = QLabel()
        self.message_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # длинный текст не растит окно
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clearMessage)
        self._style(False)

    def attach_message_label(self) -> None:
        """Сообщения — правее уже добавленных слева виджетов."""
        self.addWidget(self.message_label, 1)

    def _style(self, error: bool) -> None:
        self.message_label.setProperty("error", error)
        color = C.FAULT if error else C.TEXT_DIM
        self.message_label.setStyleSheet(f"color: {color}; font-size: {FS_CAPTION}px;")

    def showMessage(self, text: str, timeout: int = 0, error: bool = False) -> None:  # noqa: N802
        self._timer.stop()
        self._style(error)
        self.message_label.setText(text)
        self.message_label.setToolTip(text)
        if timeout > 0:
            self._timer.start(timeout)
        self.messageChanged.emit(text)

    def clearMessage(self) -> None:  # noqa: N802
        self._timer.stop()
        self._style(False)
        self.message_label.clear()
        self.message_label.setToolTip("")
        self.messageChanged.emit("")

    def currentMessage(self) -> str:  # noqa: N802
        return self.message_label.text()
