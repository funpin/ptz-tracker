from __future__ import annotations

import uuid

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ptztracker.camera.client import AuthRequired, CameraClient, CameraUnreachable
from ptztracker.camera.protocol import Q_MODEL, CameraError, parse_model
from ptztracker.config import CameraConfig, is_valid_ip


class _Signals(QObject):
    done = Signal(str)


class _ProbeTask(QRunnable):
    def __init__(self, host, user, pw, signals):
        super().__init__()
        self.host, self.user, self.pw, self.signals = host, user, pw, signals

    def run(self):
        client = CameraClient(self.host, self.user, self.pw, timeout=2.0)
        try:
            msg = f"✔ На связи: {parse_model(client.send(Q_MODEL))}"
        except AuthRequired:
            msg = "✖ Камера требует логин/пароль (или они неверны)"
        except CameraUnreachable as e:
            msg = f"✖ Нет связи: {e}"
        except (CameraError, ValueError) as e:
            msg = f"? Камера ответила, но непонятно: {e}"
        finally:
            client.close()
        self.signals.done.emit(msg)


class CameraDialog(QDialog):
    """Добавление/изменение камеры: имя, IP, логин/пароль, RTSP, интервал команд."""

    def __init__(self, cfg: CameraConfig | None = None, password: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Камера" if cfg else "Новая камера")
        self._cfg = cfg
        self._new_id = uuid.uuid4().hex[:8]
        self.name_edit = QLineEdit(cfg.name if cfg else "")
        self.ip_edit = QLineEdit(cfg.ip if cfg else "")
        self.ip_edit.setPlaceholderText("например, 10.0.0.12")
        self.user_edit = QLineEdit((cfg.username or "") if cfg else "")
        self.pass_edit = QLineEdit(password or "")
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.rtsp_edit = QLineEdit((cfg.rtsp_url or "") if cfg else "")
        self.rtsp_edit.setPlaceholderText("пусто — подобрать автоматически")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(30, 1000)
        self.interval_spin.setSuffix(" мс")
        self.interval_spin.setValue(round((cfg.min_interval if cfg else 0.130) * 1000))

        form = QFormLayout()
        form.addRow("Название", self.name_edit)
        form.addRow("IP-адрес", self.ip_edit)
        form.addRow("Логин", self.user_edit)
        form.addRow("Пароль", self.pass_edit)
        form.addRow("RTSP-адрес (необязательно)", self.rtsp_edit)
        form.addRow("Мин. интервал команд", self.interval_spin)

        self.test_button = QPushButton("Проверить связь")
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(test_row)
        lay.addWidget(buttons)

        self._signals = _Signals()
        self._signals.done.connect(self._on_probe_done)
        self.test_button.clicked.connect(self._probe)
        self.name_edit.textChanged.connect(self._validate)
        self.ip_edit.textChanged.connect(self._validate)
        self._validate()

    def _valid(self) -> bool:
        return bool(self.name_edit.text().strip()) and is_valid_ip(self.ip_edit.text())

    def _validate(self) -> None:
        ok = self._valid()
        self.ok_button.setEnabled(ok)
        self.test_button.setEnabled(is_valid_ip(self.ip_edit.text()))

    def _probe(self) -> None:
        self.test_result.setText("Проверяю…")
        QThreadPool.globalInstance().start(
            _ProbeTask(self.ip_edit.text(), self.user_edit.text() or None, self.pass_edit.text() or None, self._signals)
        )

    def _on_probe_done(self, msg: str) -> None:
        self.test_result.setText(msg)

    def result_config(self) -> CameraConfig:
        return CameraConfig(
            id=self._cfg.id if self._cfg else self._new_id,
            name=self.name_edit.text().strip(),
            ip=self.ip_edit.text(),
            kind=self._cfg.kind if self._cfg else "panasonic",
            username=self.user_edit.text().strip() or None,
            rtsp_url=self.rtsp_edit.text().strip() or None,
            min_interval=self.interval_spin.value() / 1000,
        )

    def password(self) -> str | None:
        return self.pass_edit.text() or None
