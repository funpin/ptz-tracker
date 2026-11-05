from __future__ import annotations

import dataclasses
import math
import uuid
from datetime import datetime

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ptztracker.camera.client import AuthRequired, CameraClient, CameraUnreachable
from ptztracker.camera.protocol import Q_MODEL, CameraError, parse_model
from ptztracker.config import CameraConfig, is_valid_ip
from ptztracker.tally import MAX_ATEM_INPUT, atem_input_of

_SIM_NAME = "Тренировка"  # имя, которое диалог подставляет симулятору


def model_text(cfg: CameraConfig) -> str:
    """«AW-HE40 · прошивка 1.54»; пока модель не определена — подсказка, когда это случится."""
    if not cfg.model:
        return "ещё не определена (появится после подключения)"
    return f"{cfg.model} · прошивка {cfg.firmware}" if cfg.firmware else cfg.model


_STREAM_NAMES = {"mjpeg": "MJPEG 640×360", "mjpeg_2": "MJPEG 1280×720", "h264": "H.264", "sim": "симулятор"}
_FEATURE_NAMES = {"gain": "Gain (усиление)", "wb": "баланс белого", "color_temp": "цветовая температура",
                  "power": "включение и режим ожидания"}
_NO_INFO = "прежней версии программы"


def _model_fw(model: str | None, firmware: str | None) -> str:
    return f"{model} · прошивка {firmware}" if model and firmware else (model or "")


def _calib_date(created) -> str:
    try:
        return datetime.fromisoformat(created).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return str(created) if created else "дата не записана"


def _ratio(v: float) -> str:
    return f"×{v:.2f}".replace(".", ",")


def speed_scale_text(scale) -> str:
    """«поворот ×1,40 · наклон ×1,00 · зум ×0,90»; None — поправки ещё нет."""
    if not isinstance(scale, dict):
        return "не подстраивалась (×1,00)"
    parts = []
    for key, title in (("pan", "поворот"), ("tilt", "наклон"), ("zoom", "зум")):
        v = scale.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            parts.append(f"{title} {_ratio(v)}")
    return " · ".join(parts) or "нет"


def calibration_texts(cfg: CameraConfig, reset: bool = False) -> dict[str, str]:
    """Строки раздела «Сведения» о калибровке. Читает сохранённый словарь с оглядкой: он мог быть повреждён."""
    calib = None if reset or not isinstance(cfg.calibration, dict) else cfg.calibration
    if calib is None:
        date = ("сброшена (применится после «ОК»)" if reset
                else "не делалась (используется таблица по умолчанию)")
        return dict(calib_date=date, calib_stream="—", calib_model="—", calib_zoom="—", calib_tilt="—")
    pts = calib.get("zoom_points")
    pts = [z for z in pts if isinstance(z, int)] if isinstance(pts, list) else []
    model = calib.get("model")
    fw = calib.get("firmware")
    return dict(
        calib_date=_calib_date(calib.get("created")),
        calib_stream=_STREAM_NAMES.get(calib.get("stream_kind"), "не записан"),
        calib_model=(_model_fw(model, fw) or model) if isinstance(model, str) and model
        else f"не записана (калибровка из {_NO_INFO})",
        calib_zoom=f"{len(pts)} ({', '.join(f'0x{z:X}' for z in sorted(pts))})" if pts else "нет данных",
        calib_tilt="измерен" if calib.get("tilt") else "не измерен",
    )


def unsupported_text(features) -> str:
    return ", ".join(_FEATURE_NAMES.get(f, f) for f in features) or "нет"


def merge_edit(latest: CameraConfig, snapshot: CameraConfig, result: CameraConfig) -> CameraConfig:
    """Применить к свежей записи камеры только то, что оператор изменил в диалоге (result против snapshot,
    с которым диалог открылся). Всё, что окно успело узнать за время диалога (модель, поправка скорости), остаётся."""
    changes = {f.name: getattr(result, f.name) for f in dataclasses.fields(CameraConfig)
               if getattr(result, f.name) != getattr(snapshot, f.name)}
    if result.ip != snapshot.ip:  # другой адрес — возможно, другая камера: прежние сведения о ней не годятся
        changes.update(model=None, firmware=None, unsupported=[])
    changes["unsupported"] = list(changes.get("unsupported", latest.unsupported))  # копия, не общий список
    return dataclasses.replace(latest, **changes)


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
        # снимок на момент открытия: с ним окно сравнивает итог (merge_edit); список — копия, не общий
        self._cfg = dataclasses.replace(cfg, unsupported=list(cfg.unsupported)) if cfg else None
        self._reset_calibration = False
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

        settings = QWidget()
        sl = QVBoxLayout(settings)
        sl.addLayout(form)
        sl.addLayout(test_row)
        sl.addStretch(1)

        # «Сведения»: только показ (модель и прошивку находит сама программа); у новой камеры и симулятора их нет
        self._has_info = cfg is not None and cfg.kind != "sim"
        self.tabs = QTabWidget()
        self.tabs.addTab(settings, "Настройки")
        self.tabs.addTab(self._build_info(), "Сведения")
        self.tabs.setTabVisible(1, self._has_info)
        self.tabs.tabBar().setVisible(self._has_info)
        if not self._has_info:
            self.model_label.hide()
        self._refresh_info()

        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        lay.addWidget(buttons)

        self._signals = _Signals()
        self._signals.done.connect(self._on_probe_done)
        self.test_button.clicked.connect(self._probe)
        self.name_edit.textChanged.connect(self._validate)
        self.ip_edit.textChanged.connect(self._validate)
        self.kind_combo.currentIndexChanged.connect(self._on_kind)
        self._on_kind()

    @property
    def snapshot(self) -> CameraConfig | None:
        """Камера такой, какой её открыли (для merge_edit)."""
        return self._cfg

    def _build_info(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.info_values: dict[str, QLabel] = {}
        for key, title in (
            ("model", "Модель камеры"), ("calib_date", "Калибровка сделана"), ("calib_stream", "Поток при калибровке"),
            ("calib_model", "Калибровали на"), ("calib_zoom", "Точки зума"), ("calib_tilt", "Наклон"),
            ("speed_scale", "Поправка скорости"), ("unsupported", "Камера не умеет"),
        ):
            lab = QLabel()
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.info_values[key] = lab
            form.addRow(title, lab)
        self.model_label = self.info_values["model"]
        self.reset_button = QPushButton("Сбросить калибровку")
        self.reset_button.setToolTip("Забыть калибровку и поправку скорости только этой камеры. "
                                     "Применится после «ОК»; слежение вернётся к таблице по умолчанию")
        self.reset_button.clicked.connect(self._on_reset_calibration)
        form.addRow(self.reset_button)
        return page

    def _refresh_info(self) -> None:
        cfg = self._cfg
        if cfg is None:
            return
        texts = calibration_texts(cfg, self._reset_calibration)
        texts["model"] = model_text(cfg)
        texts["speed_scale"] = "сброшена" if self._reset_calibration else speed_scale_text(cfg.speed_scale)
        texts["unsupported"] = unsupported_text(cfg.unsupported)
        for k, v in texts.items():
            self.info_values[k].setText(v)
        self.reset_button.setEnabled(
            not self._reset_calibration and (cfg.calibration is not None or cfg.speed_scale is not None))

    def _on_reset_calibration(self) -> None:
        name = self.name_edit.text().strip() or "этой камеры"
        q = (f"Сбросить калибровку и поправку скорости камеры «{name}»?\n"
             "Другие камеры не затрагиваются. Потом камеру лучше откалибровать заново.")
        if QMessageBox.question(self, "Сбросить калибровку", q) == QMessageBox.Yes:
            self._reset_calibration = True
            self._refresh_info()

    def _is_sim(self) -> bool:
        return self.kind_combo.currentData() == "sim"

    def _on_kind(self, *_) -> None:
        sim = self._is_sim()
        for w in (self.ip_edit, self.user_edit, self.pass_edit, self.rtsp_edit, self.interval_spin, self.atem_spin):
            self._form.setRowVisible(w, not sim)
        self.test_button.setVisible(not sim)
        self.test_result.setVisible(not sim)
        if sim and not self.name_edit.text().strip():
            self.name_edit.setText(_SIM_NAME)
        elif not sim and self.name_edit.text() == _SIM_NAME:
            self.name_edit.clear()  # подставленное имя симулятора настоящей камере не подходит
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
        """Камера по полям диалога. Незатронутое (калибровка, поправка скорости, композиция, усилие, поток,
        модель, прошивка, возможности) сохраняется; при другом IP-адресе сведения о камере забываются."""
        keep = {} if self._cfg is None else dict(unsupported=list(self._cfg.unsupported))
        if self._cfg is not None and self._reset_calibration:
            keep.update(calibration=None, speed_scale=None)
        if self._is_sim():  # виртуальная камера: сети нет, интервал и ATEM остаются прежними
            fields = dict(name=self.name_edit.text().strip(), ip="sim", username=None, rtsp_url=None,
                          pad_number=self.pad_spin.value() or None)
            if self._cfg is not None:
                return dataclasses.replace(self._cfg, **fields, **keep)
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
        if self._cfg is not None:
            if fields["ip"] != self._cfg.ip:  # по новому адресу может ответить другая камера: сессия определит заново
                keep.update(model=None, firmware=None, unsupported=[])
            return dataclasses.replace(self._cfg, **fields, **keep)
        return CameraConfig(id=self._new_id, kind="panasonic", **fields)

    def password(self) -> str | None:
        return None if self._is_sim() else (self.pass_edit.text() or None)
