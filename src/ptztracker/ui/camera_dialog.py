from __future__ import annotations

import dataclasses
import uuid

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox,
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
from ptztracker.tally import MAX_ATEM_INPUT, atem_input_of


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
    """Добавление/изменение камеры: тип, имя, IP, логин/пароль, RTSP, интервал команд.
    У симулятора (виртуальной камеры для тренировки) IP, логин, RTSP и связь не нужны."""

    def __init__(self, cfg: CameraConfig | None = None, password: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Камера" if cfg else "Новая камера")
        self._cfg = cfg
        self._new_id = uuid.uuid4().hex[:8]
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("Panasonic AW-HE", "panasonic")
        self.kind_combo.addItem("Симулятор", "sim")
        self.kind_combo.setCurrentIndex(self.kind_combo.findData(cfg.kind if cfg else "panasonic"))
        self.kind_combo.setEnabled(cfg is None)  # тип у готовой камеры не меняют: настройки у них разные
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
        self.atem_spin = QSpinBox()
        self.atem_spin.setRange(0, MAX_ATEM_INPUT)
        self.atem_spin.setSpecialValueText("нет")
        self.atem_spin.setValue(atem_input_of(cfg) if cfg else 0)

        self.pad_spin = QSpinBox()
        self.pad_spin.setRange(0, 4)
        self.pad_spin.setSpecialValueText("авто")
        self.pad_spin.setToolTip("Какими белыми точками светится геймпад для этой камеры. Авто — цифры в конце названия, "
                                 "иначе место в списке")
        self.pad_spin.setValue((cfg.pad_number or 0) if cfg else 0)

        form = self._form = QFormLayout()
        form.addRow("Тип камеры", self.kind_combo)
        form.addRow("Название", self.name_edit)
        form.addRow("IP-адрес", self.ip_edit)
        form.addRow("Логин", self.user_edit)
        form.addRow("Пароль", self.pass_edit)
        form.addRow("RTSP-адрес (необязательно)", self.rtsp_edit)
        form.addRow("Мин. интервал команд", self.interval_spin)
        form.addRow("Вход ATEM", self.atem_spin)
        form.addRow("Номер на геймпаде", self.pad_spin)

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
        self.kind_combo.currentIndexChanged.connect(self._on_kind)
        self._on_kind()

    def _is_sim(self) -> bool:
        return self.kind_combo.currentData() == "sim"

    def _on_kind(self, *_) -> None:
        sim = self._is_sim()
        for w in (self.ip_edit, self.user_edit, self.pass_edit, self.rtsp_edit, self.interval_spin, self.atem_spin):
            self._form.setRowVisible(w, not sim)
        self.test_button.setVisible(not sim)
        self.test_result.setVisible(not sim)
        if sim and not self.name_edit.text().strip():
            self.name_edit.setText("Тренировка")
        self._validate()

    def _valid(self) -> bool:
        if not self.name_edit.text().strip():
            return False
        return self._is_sim() or is_valid_ip(self.ip_edit.text())

    def _validate(self) -> None:
        self.ok_button.setEnabled(self._valid())
        self.test_button.setEnabled(is_valid_ip(self.ip_edit.text()))

    def _probe(self) -> None:
        self.test_result.setText("Проверяю…")
        QThreadPool.globalInstance().start(
            _ProbeTask(self.ip_edit.text(), self.user_edit.text() or None, self.pass_edit.text() or None, self._signals)
        )

    def _on_probe_done(self, msg: str) -> None:
        self.test_result.setText(msg)

    def result_config(self) -> CameraConfig:
        if self._is_sim():  # виртуальная камера: сети нет, интервал и ATEM остаются прежними
            fields = dict(name=self.name_edit.text().strip(), ip="sim", username=None, rtsp_url=None,
                          pad_number=self.pad_spin.value() or None)
            if self._cfg is not None:
                return dataclasses.replace(self._cfg, **fields)
            return CameraConfig(id=self._new_id, kind="sim", **fields)
        fields = dict(
            name=self.name_edit.text().strip(),
            ip=self.ip_edit.text(),
            username=self.user_edit.text().strip() or None,
            rtsp_url=self.rtsp_edit.text().strip() or None,
            min_interval=self.interval_spin.value() / 1000,
            atem_input=self.atem_spin.value(),
            pad_number=self.pad_spin.value() or None,
        )
        if self._cfg is not None:  # калибровка, поправка скорости, композиция, усилие, поток сохраняются
            return dataclasses.replace(self._cfg, **fields)
        return CameraConfig(id=self._new_id, kind="panasonic", **fields)

    def password(self) -> str | None:
        return None if self._is_sim() else (self.pass_edit.text() or None)
