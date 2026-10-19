from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ptztracker.camera.session import CameraSession
from ptztracker.config import AppConfig, CameraConfig, ConfigStore, get_password, set_password
from ptztracker.ui.camera_dialog import CameraDialog
from ptztracker.ui.manual_controls import ManualControls
from ptztracker.ui.preview import PreviewWidget
from ptztracker.video.rtsp import STREAM_CHOICES, strip_credentials

log = logging.getLogger(__name__)

SIM_CAMERA = CameraConfig(id="sim", name="Симулятор", ip="sim", kind="sim")
KEY_PAN_SPEED = 0.4
KEY_ZOOM_SPEED = 0.5
_PAN_KEYS = {Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down}
_ZOOM_IN_KEYS = {Qt.Key_Plus, Qt.Key_Equal}
_ZOOM_OUT_KEYS = {Qt.Key_Minus, Qt.Key_Underscore}


class MainWindow(QMainWindow):
    def __init__(self, store: ConfigStore, use_sim: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PTZ Tracker")
        self.resize(1400, 820)
        self.store = store
        self.cfg: AppConfig = store.load()
        self.use_sim = use_sim
        self.session: CameraSession | None = None
        self._held: set[int] = set()

        self.preview = PreviewWidget()
        self.camera_combo = QComboBox()
        self.add_btn = QPushButton("Добавить")
        self.edit_btn = QPushButton("Изменить")
        self.del_btn = QPushButton("Удалить")
        cam_box = QGroupBox("Камера")
        cl = QVBoxLayout(cam_box)
        cl.addWidget(self.camera_combo)
        row = QHBoxLayout()
        for b in (self.add_btn, self.edit_btn, self.del_btn):
            row.addWidget(b)
        cl.addLayout(row)
        self.stream_combo = QComboBox()
        for key, label in STREAM_CHOICES.items():
            self.stream_combo.addItem(label, key)
        self.stream_combo.setToolTip(
            "Какой поток камеры показывать и анализировать. Меньшее разрешение — обычно меньше задержка."
        )
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Видео"))
        srow.addWidget(self.stream_combo, 1)
        cl.addLayout(srow)
        self.controls = ManualControls()

        side = QWidget()
        side.setFixedWidth(340)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(cam_box)
        sl.addWidget(self.controls)
        sl.addStretch(1)

        central = QWidget()
        hl = QHBoxLayout(central)
        hl.addWidget(self.preview, 1)
        hl.addWidget(side)
        self.setCentralWidget(central)

        self.link_label = QLabel()
        self.video_label = QLabel()
        self.statusBar().addWidget(self.link_label)
        self.statusBar().addWidget(self.video_label, 1)
        self.statusBar().addPermanentWidget(QLabel("Esc — стоп · стрелки — поворот · +/− — зум"))

        self.service_menu = self.menuBar().addMenu("Сервис")
        self.diag_action = QAction("Диагностика камеры…", self)
        self.service_menu.addAction(self.diag_action)
        self.diag_action.triggered.connect(self._run_diagnostics)

        self.add_btn.clicked.connect(self._add_camera)
        self.edit_btn.clicked.connect(self._edit_camera)
        self.del_btn.clicked.connect(self._delete_camera)
        self.camera_combo.currentIndexChanged.connect(self._on_combo)
        self.stream_combo.currentIndexChanged.connect(self._on_stream_changed)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(500)

        QApplication.instance().installEventFilter(self)
        self._fill_combo(select_id="sim" if use_sim else self.cfg.last_camera_id)

    # --- список камер -------------------------------------------------

    def _cameras(self) -> list[CameraConfig]:
        return self.cfg.cameras + ([SIM_CAMERA] if self.use_sim else [])

    def _fill_combo(self, select_id: str | None) -> None:
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        for c in self._cameras():
            label = c.name if c.kind == "sim" else f"{c.name} · {c.ip}"
            self.camera_combo.addItem(label, c.id)
        idx = self.camera_combo.findData(select_id) if select_id else -1
        self.camera_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.camera_combo.blockSignals(False)
        self._on_combo()

    def _current_cfg(self) -> CameraConfig | None:
        cid = self.camera_combo.currentData()
        return next((c for c in self._cameras() if c.id == cid), None)

    def _on_combo(self, *_):
        self._activate(self._current_cfg())

    def _activate(self, cam: CameraConfig | None) -> None:
        self.release_all_held()
        if self.session:
            self.session.stop()
            self.session = None
        self.preview.set_source(None)
        self.controls.bind(None)
        self.edit_btn.setEnabled(cam is not None and cam.kind != "sim")
        self.del_btn.setEnabled(cam is not None and cam.kind != "sim")
        self.stream_combo.blockSignals(True)
        self.stream_combo.setCurrentIndex(max(0, self.stream_combo.findData(cam.stream if cam else "auto")))
        self.stream_combo.blockSignals(False)
        self.stream_combo.setEnabled(cam is not None and cam.kind != "sim")
        if cam is None:
            return
        self.session = CameraSession(cam, scene_path=self.cfg.sim_scene_path)
        self.session.start()
        self.preview.set_source(self.session.frames)
        self.controls.bind(self.session)
        if cam.kind != "sim":
            self.cfg.last_camera_id = cam.id
            self._save()

    def _on_stream_changed(self, *_):
        cam = self._current_cfg()
        if not cam or cam.kind == "sim":
            return
        cam.stream = self.stream_combo.currentData()
        self._save()
        self._activate(cam)

    def _save(self) -> None:
        try:
            self.store.save(self.cfg)
        except OSError as e:
            log.error("Не удалось сохранить настройки: %s", e)
            self.statusBar().showMessage(f"Не удалось сохранить настройки: {e}", 5000)

    def _add_camera(self) -> None:
        d = CameraDialog(parent=self)
        if d.exec():
            cam = d.result_config()
            set_password(cam.id, d.password())
            self.cfg.cameras.append(cam)
            self._save()
            self._fill_combo(cam.id)

    def _edit_camera(self) -> None:
        cur = self._current_cfg()
        if not cur or cur.kind == "sim":
            return
        d = CameraDialog(cur, get_password(cur.id) if cur.username else None, parent=self)
        if d.exec():
            cam = d.result_config()
            set_password(cam.id, d.password())
            self.cfg.cameras = [cam if c.id == cam.id else c for c in self.cfg.cameras]
            self._save()
            self._fill_combo(cam.id)

    def _delete_camera(self) -> None:
        cur = self._current_cfg()
        if not cur or cur.kind == "sim":
            return
        if QMessageBox.question(self, "Удалить камеру", f"Удалить «{cur.name}» из списка?") != QMessageBox.Yes:
            return
        self.cfg.cameras = [c for c in self.cfg.cameras if c.id != cur.id]
        set_password(cur.id, None)
        self._save()
        self._fill_combo(None)

    def _run_diagnostics(self) -> None:
        from ptztracker.ui.diagnostics_dialog import DiagnosticsDialog

        cam = self._current_cfg()
        if not cam or cam.kind == "sim":
            QMessageBox.information(self, "Диагностика", "Выберите реальную камеру из списка.")
            return
        # на время диагностики программа не управляет камерой
        self._activate(None)
        password = get_password(cam.id) if cam.username else None
        DiagnosticsDialog(cam.ip, cam.username, password, parent=self).exec()
        self._activate(cam)

    # --- статус -------------------------------------------------------

    def _update_status(self) -> None:
        s = self.session
        if not s:
            self.link_label.setText("Камера не выбрана")
            self.video_label.setText("")
            return
        self._remember_rtsp_url(s)
        self.link_label.setText("● на связи" if s.online else "○ нет связи с камерой")
        self.link_label.setStyleSheet("color: #22c55e;" if s.online else "color: #ef4444;")
        st = s.frames.stats()
        if st.connected:
            self.video_label.setText(f"видео {st.width}×{st.height}, {st.fps:.0f} к/с")
        else:
            self.video_label.setText(st.error or "нет видео")

    def _remember_rtsp_url(self, s: CameraSession) -> None:
        """Сохранить подобранный адрес видео (без пароля), чтобы в следующий раз открывался сразу."""
        found = s.found_rtsp_url
        if not found:
            return
        s.found_rtsp_url = None
        url = strip_credentials(found)
        cam = next((c for c in self.cfg.cameras if c.id == s.cfg.id), None)
        if cam and cam.rtsp_url != url:
            cam.rtsp_url = url
            self._save()
            self.statusBar().showMessage(f"Найден адрес видео: {url}", 8000)

    # --- горячие клавиши ---------------------------------------------

    def _is_mine(self, obj) -> bool:
        return obj is self or (isinstance(obj, QWidget) and self.isAncestorOf(obj))

    def eventFilter(self, obj, event):  # noqa: N802
        et = event.type()
        if et in (QEvent.KeyPress, QEvent.KeyRelease) and self._is_mine(obj):
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QAbstractSpinBox)):
                return False
            key = event.key()
            if key == Qt.Key_Escape and et == QEvent.KeyPress:
                self.release_all_held()
                if self.session:
                    self.session.emergency_stop()
                return True
            if key in _PAN_KEYS | _ZOOM_IN_KEYS | _ZOOM_OUT_KEYS:
                if not event.isAutoRepeat():
                    if et == QEvent.KeyPress:
                        self._held.add(key)
                    else:
                        self._held.discard(key)
                    self._apply_held()
                return True
        return super().eventFilter(obj, event)

    def _apply_held(self) -> None:
        if not self.session:
            return
        h = self._held
        vx = KEY_PAN_SPEED * ((Qt.Key_Right in h) - (Qt.Key_Left in h))
        vy = KEY_PAN_SPEED * ((Qt.Key_Up in h) - (Qt.Key_Down in h))
        vz = KEY_ZOOM_SPEED * (bool(h & _ZOOM_IN_KEYS) - bool(h & _ZOOM_OUT_KEYS))
        self.session.set_pan_tilt_velocity(vx, vy)
        self.session.set_zoom_velocity(vz)

    def release_all_held(self) -> None:
        """Отпустить все удерживаемые клавиши — камера останавливается."""
        if self._held:
            self._held.clear()
            self._apply_held()
        self.controls.release_all()

    def changeEvent(self, e):  # noqa: N802
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.release_all_held()
        super().changeEvent(e)

    def closeEvent(self, e):  # noqa: N802
        QApplication.instance().removeEventFilter(self)
        self._status_timer.stop()
        self.release_all_held()
        if self.session:
            self.session.stop()
            self.session = None
        self._save()
        super().closeEvent(e)
