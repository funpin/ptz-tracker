from __future__ import annotations

import dataclasses
import math
import uuid
from datetime import datetime

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ptztracker.camera.client import AuthRequired, CameraClient, CameraUnreachable
from ptztracker.camera.protocol import Q_MODEL, CameraError, parse_model
from ptztracker.config import CameraConfig, is_valid_ip
from ptztracker.control.camera_model import zoom_ln
from ptztracker.tally import MAX_ATEM_INPUT, atem_input_of
from ptztracker.ui.theme import FS_CAPTION, R_PANEL, SP, C

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


def _zoom_points_text(pts: list[int]) -> str:
    """«3 точки (от широкого до ×8,5)»: сколько положений зума измерено и до какого увеличения."""
    if not pts:
        return "нет данных"
    n = len(pts)
    word = "точка" if n % 10 == 1 and n % 100 != 11 else "точки" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "точек"
    top = math.exp(max(0.0, zoom_ln(max(pts))))
    return f"{n} {word} (от широкого до ×{top:.1f})".replace(".", ",")


def calibration_texts(cfg: CameraConfig, reset: bool = False) -> dict[str, str]:
    """Строки раздела «Сведения» о калибровке. Читает сохранённый словарь с оглядкой: он мог быть повреждён."""
    calib = None if reset or not isinstance(cfg.calibration, dict) else cfg.calibration
    if calib is None:
        date = ("сброшена (применится после «ОК»)" if reset
                else "не делалась (используются стандартные настройки)")
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
        calib_zoom=_zoom_points_text(pts),
        calib_tilt="измерен" if calib.get("tilt") else "не измерен",
    )


def unsupported_text(features) -> str:
    return ", ".join(_FEATURE_NAMES.get(f, f) for f in features) or "нет"


def merge_edit(latest: CameraConfig, snapshot: CameraConfig, result: CameraConfig,
               reset_calibration: bool = False) -> CameraConfig:
    """Применить к свежей записи камеры только то, что оператор изменил в диалоге (result против snapshot,
    с которым диалог открылся). Всё, что окно успело узнать за время диалога (модель, поправка скорости), остаётся.
    Подтверждённый сброс калибровки снимает калибровку и поправку скорости и в свежей записи, даже если слежение
    записало поправку, пока диалог был открыт."""
    changes = {f.name: getattr(result, f.name) for f in dataclasses.fields(CameraConfig)
               if getattr(result, f.name) != getattr(snapshot, f.name)}
    if result.ip != snapshot.ip:  # другой адрес — возможно, другая камера: прежние сведения о ней не годятся
        changes.update(model=None, firmware=None, unsupported=[])
    if reset_calibration:
        changes.update(calibration=None, speed_scale=None)
    changes["unsupported"] = list(changes.get("unsupported", latest.unsupported))  # копия, не общий список
    return dataclasses.replace(latest, **changes)


LABEL_W = 120


def _form(parent=None) -> QFormLayout:
    f = QFormLayout(parent)
    f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    f.setHorizontalSpacing(SP[2])
    f.setVerticalSpacing(SP[1])
    f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    return f


def _section(title: str) -> QLabel:
    lab = QLabel(title.upper())
    lab.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: 11px; font-weight: 600; letter-spacing: 1px;"
                      " margin-top: 4px;")
    return lab


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
    """Добавление/изменение камеры: тип, имя, подключение, эфир, «Дополнительно» (RTSP, интервал), карточка
    калибровки — одна страница по макету docs/design/mockups/menu-camera-diag.html.
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

        self.tracking_check = QCheckBox("нейросеть ведёт людей в этой камере")
        self.tracking_check.setChecked(cfg.tracking if cfg else True)

        # тип — две кнопки-переключателя; список kind_combo остаётся моделью (его читают окно и тесты)
        self.kind_combo.setParent(self)
        self.kind_combo.hide()
        self.kind_buttons: dict[str, QPushButton] = {}
        kind_row = QHBoxLayout()
        kind_row.setSpacing(0)
        for i in range(self.kind_combo.count()):
            key = self.kind_combo.itemData(i)
            b = QPushButton(self.kind_combo.itemText(i))
            b.setProperty("variant", "seg")
            b.setCheckable(True)
            b.setAutoDefault(False)
            b.setEnabled(self.kind_combo.isEnabled())
            b.clicked.connect(lambda _=False, k=key: self.kind_combo.setCurrentIndex(self.kind_combo.findData(k)))
            self.kind_buttons[key] = b
            kind_row.addWidget(b)
        kind_row.addStretch(1)

        self.test_button = QPushButton("Проверить связь")
        self.test_button.setAutoDefault(False)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        self.show_pass = QPushButton("показать")  # обычная кнопка, как «Проверить связь»: ghost читался как подпись
        self.show_pass.setCheckable(True)
        self.show_pass.setAutoDefault(False)
        self.show_pass.toggled.connect(
            lambda on: (self.pass_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password),
                        self.show_pass.setText("скрыть" if on else "показать")))
        ip_row = QHBoxLayout()
        ip_row.setSpacing(SP[1])
        ip_row.addWidget(self.ip_edit, 1)
        ip_row.addWidget(self.test_button)
        pass_row = QHBoxLayout()
        pass_row.setSpacing(SP[1])
        pass_row.addWidget(self.pass_edit, 1)
        pass_row.addWidget(self.show_pass)

        top = self._form = _form()
        top.addRow("Тип", kind_row)
        top.addRow("Название", self.name_edit)
        self.conn_title = _section("Подключение")
        conn = self._conn_form = _form()
        conn.addRow("IP-адрес", ip_row)
        conn.addRow("", self.test_result)
        conn.addRow("Логин", self.user_edit)
        conn.addRow("Пароль", pass_row)
        self.air_title = _section("Эфир")
        air = self._air_form = _form()
        air.addRow("Вход ATEM", self.atem_spin)
        air.addRow("Слежение", self.tracking_check)
        self.atem_spin.setMaximumWidth(120)
        self.interval_spin.setMaximumWidth(120)
        # редкое — под «Дополнительно»: менять, только если просит разработчик
        self.more_button = QPushButton("▸ Дополнительно — RTSP и интервал команд")
        self.more_button.setProperty("variant", "ghost")
        self.more_button.setCheckable(True)
        self.more_button.setAutoDefault(False)
        self.more_box = QWidget()
        adv = _form(self.more_box)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.addRow("RTSP-адрес", self.rtsp_edit)
        adv.addRow("Интервал команд", self.interval_spin)
        self.more_box.hide()
        self.more_button.toggled.connect(self._toggle_more)

        # «Отмена» слева от главной белой кнопки — одинаково на Mac и Windows
        self.ok_button = QPushButton("Сохранить" if cfg else "Добавить камеру")
        self.ok_button.setProperty("variant", "primary")
        self.ok_button.setDefault(True)
        cancel = QPushButton("Отмена")
        cancel.setAutoDefault(False)
        self.ok_button.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.setSpacing(SP[2])
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_button)
        # «Сведения» о калибровке — карточкой в том же окне; у новой камеры и симулятора её нет
        self._has_info = cfg is not None and cfg.kind != "sim"
        self.calib_card = self._build_info()
        self.calib_card.setVisible(self._has_info)
        if not self._has_info:
            self.model_label.hide()
        self._refresh_info()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[3], SP[3], SP[3], SP[3])
        lay.setSpacing(SP[2])
        for part in (top, self.conn_title, conn, self.air_title, air):
            (lay.addLayout if isinstance(part, QFormLayout) else lay.addWidget)(part)
        lay.addWidget(self.more_button, 0, Qt.AlignLeft)
        lay.addWidget(self.more_box)
        lay.addWidget(self.calib_card)
        lay.addStretch(1)
        lay.addLayout(buttons)
        self.setMinimumWidth(540)
        for f in (top, conn, air, adv):  # подписи всех разделов — одной ширины, поля встают в один столбец
            for i in range(f.rowCount()):
                item = f.itemAt(i, QFormLayout.LabelRole)
                if item is not None and item.widget() is not None:
                    item.widget().setFixedWidth(LABEL_W)
        self._signals = _Signals()
        self._signals.done.connect(self._on_probe_done)
        self.test_button.clicked.connect(self._probe)
        self.name_edit.textChanged.connect(self._validate)
        self.ip_edit.textChanged.connect(self._validate)
        self.kind_combo.currentIndexChanged.connect(self._on_kind)
        self._on_kind()

    @property
    def reset_calibration(self) -> bool:
        """Оператор подтвердил «Сбросить калибровку» (применится после «ОК»)."""
        return self._reset_calibration

    @property
    def snapshot(self) -> CameraConfig | None:
        """Камера такой, какой её открыли (для merge_edit)."""
        return self._cfg

    def _build_info(self) -> QWidget:
        card = QFrame()
        card.setObjectName("calibCard")
        card.setStyleSheet(f"QFrame#calibCard {{ background: {C.SURFACE}; border: 1px solid {C.LINE};"
                           f" border-radius: {R_PANEL}px; }}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, SP[1], 0, SP[1])
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(SP[2], 0, SP[1], SP[0])
        head.addWidget(_section("Сведения и калибровка"))
        head.addStretch(1)
        self.reset_button = QPushButton("Сбросить калибровку…")
        self.reset_button.setProperty("variant", "ghost")
        self.reset_button.setAutoDefault(False)
        self.reset_button.setStyleSheet(f"font-size: {FS_CAPTION}px; color: {C.TEXT_DIM}; min-height: 24px;")
        self.reset_button.setToolTip("Забыть калибровку и поправку скорости только этой камеры. "
                                     "Применится после «Сохранить»; слежение вернётся к стандартным настройкам")
        self.reset_button.clicked.connect(self._on_reset_calibration)
        head.addWidget(self.reset_button)
        lay.addLayout(head)
        self.info_values: dict[str, QLabel] = {}
        for i, (key, title) in enumerate((
            ("model", "Модель камеры"), ("calib_date", "Калибровка сделана"), ("calib_stream", "Поток при калибровке"),
            ("calib_model", "Калибровали на"), ("calib_zoom", "Точки зума"), ("calib_tilt", "Наклон"),
            ("speed_scale", "Поправка скорости"), ("unsupported", "Камера не умеет"),
        )):
            row = QWidget()
            row.setStyleSheet(f"background: {'rgba(245,245,242,0.035)' if i % 2 else 'transparent'};")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(SP[2], 3, SP[2], 3)
            name = QLabel(title)
            name.setFixedWidth(160)
            name.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
            lab = QLabel()
            lab.setWordWrap(True)
            lab.setStyleSheet(f"font-size: {FS_CAPTION}px;")
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            rl.addWidget(name)
            rl.addWidget(lab, 1)
            self.info_values[key] = lab
            lay.addWidget(row)
        self.model_label = self.info_values["model"]
        return card

    def _toggle_more(self, on: bool) -> None:
        self.more_box.setVisible(on)
        self.more_button.setText(("▾" if on else "▸") + " Дополнительно — RTSP и интервал команд")

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
        for key, b in self.kind_buttons.items():
            b.setChecked(key == self.kind_combo.currentData())
        # у симулятора нет сети и ATEM: остаются тип, название и слежение
        for w in (self.conn_title, self.air_title):
            w.setVisible(not sim)
        for i in range(self._conn_form.rowCount()):
            self._conn_form.setRowVisible(i, not sim)
        self._conn_form.setRowVisible(self.test_result, not sim and bool(self.test_result.text()))  # пустая — без зазора
        self._air_form.setRowVisible(self.atem_spin, not sim)
        self.more_button.setVisible(not sim)
        if sim:
            self.more_button.setChecked(False)
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
        self.test_result.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.test_result.setText("Проверяю…")
        self._conn_form.setRowVisible(self.test_result, True)
        QThreadPool.globalInstance().start(
            _ProbeTask(self.ip_edit.text(), self.user_edit.text() or None, self.pass_edit.text() or None, self._signals)
        )

    def _on_probe_done(self, msg: str) -> None:
        # ответ строкой под адресом: лайм — на связи, красный — нет связи или пароль, оранжевый — непонятный ответ
        color = {"✔": C.LIME, "✖": C.FAULT}.get(msg[:1], C.LOST)
        self.test_result.setStyleSheet(f"color: {color};")
        self.test_result.setText(msg)
        self._conn_form.setRowVisible(self.test_result, True)

    def result_config(self) -> CameraConfig:
        """Камера по полям диалога. Незатронутое (калибровка, поправка скорости, композиция, усилие, поток,
        модель, прошивка, возможности) сохраняется; при другом IP-адресе сведения о камере забываются."""
        keep = {} if self._cfg is None else dict(unsupported=list(self._cfg.unsupported))
        if self._cfg is not None and self._reset_calibration:
            keep.update(calibration=None, speed_scale=None)
        if self._is_sim():  # виртуальная камера: сети нет, интервал и ATEM остаются прежними
            fields = dict(name=self.name_edit.text().strip(), ip="sim", username=None, rtsp_url=None)
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
            tracking=self.tracking_check.isChecked(),
        )
        if self._cfg is not None:
            if fields["ip"] != self._cfg.ip:  # по новому адресу может ответить другая камера: сессия определит заново
                keep.update(model=None, firmware=None, unsupported=[])
            return dataclasses.replace(self._cfg, **fields, **keep)
        return CameraConfig(id=self._new_id, kind="panasonic", **fields)

    def password(self) -> str | None:
        return None if self._is_sim() else (self.pass_edit.text() or None)
