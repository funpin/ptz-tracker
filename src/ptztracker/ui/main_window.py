from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ptztracker.camera.session import CameraSession
from ptztracker.config import AppConfig, CameraConfig, ConfigStore, get_password, set_password
from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.ui.camera_dialog import CameraDialog
from ptztracker.ui.follow_panel import FollowPanel
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
    _saveFinished = Signal(object)  # Future записи; приходит из рабочего потока, обрабатывается в Qt-потоке

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

        self.follow_panel = FollowPanel()
        target_box = QGroupBox("Цель")
        tl = QVBoxLayout(target_box)
        self.target_label = QLabel("Цель: не выбрана — кликните по человеку на видео")
        self.target_label.setWordWrap(True)
        self.reset_target_btn = QPushButton("Сбросить цель")
        self.skeleton_check = QCheckBox("Показывать скелет")
        trow = QHBoxLayout()
        trow.addWidget(self.reset_target_btn)
        trow.addWidget(self.skeleton_check)
        tl.addWidget(self.target_label)
        tl.addLayout(trow)

        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self.follow_panel)
        sl.addWidget(target_box)
        sl.addWidget(cam_box)
        sl.addWidget(self.controls)
        sl.addStretch(1)

        # боковая панель прокручивается — окно помещается и на небольших экранах
        side_scroll = QScrollArea()
        side_scroll.setWidget(side)
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side_scroll.setFrameShape(QScrollArea.NoFrame)
        side_scroll.setFixedWidth(362)
        side.setFixedWidth(345)

        central = QWidget()
        hl = QHBoxLayout(central)
        hl.addWidget(self.preview, 1)
        hl.addWidget(side_scroll)
        self.setCentralWidget(central)

        self.link_label = QLabel()
        self.video_label = QLabel()
        self.analysis_label = QLabel()
        self.statusBar().addWidget(self.link_label)
        self.statusBar().addWidget(self.video_label)
        self.statusBar().addWidget(self.analysis_label, 1)
        self.record_label = QLabel()
        self.record_label.setStyleSheet("color: #ef4444;")
        self.show_folder_btn = QPushButton("Показать в папке")
        self.show_folder_btn.hide()
        self.statusBar().addWidget(self.record_label)
        self.statusBar().addWidget(self.show_folder_btn)
        self.statusBar().addPermanentWidget(QLabel("Esc — стоп · стрелки — поворот · +/− — зум"))

        self.service_menu = self.menuBar().addMenu("Сервис")
        self.diag_action = QAction("Диагностика камеры…", self)
        self.service_menu.addAction(self.diag_action)
        self.diag_action.triggered.connect(self._run_diagnostics)
        self.calib_action = QAction("Калибровка камеры…", self)
        self.service_menu.addAction(self.calib_action)
        self.calib_action.triggered.connect(self._run_calibration)
        self.save_moment_action = QAction("Сохранить момент (F9)", self)
        self.save_moment_action.setShortcut(Qt.Key_F9)  # это не ручное управление: слежение не трогаем
        self.service_menu.addAction(self.save_moment_action)
        self.save_moment_action.triggered.connect(self.save_moment)
        self.rec_settings_action = QAction("Настройки записи…", self)
        self.service_menu.addAction(self.rec_settings_action)
        self.rec_settings_action.triggered.connect(self._recording_settings)
        self.open_replay_action = QAction("Открыть запись…", self)
        self.service_menu.addAction(self.open_replay_action)
        self.open_replay_action.triggered.connect(self._choose_replay)
        self._saveFinished.connect(self._on_save_finished)
        self._saved_path: Path | None = None
        self._hide_folder_timer = QTimer(self)
        self._hide_folder_timer.setSingleShot(True)
        self._hide_folder_timer.timeout.connect(self.show_folder_btn.hide)
        self.show_folder_btn.clicked.connect(self._show_saved_folder)

        self.add_btn.clicked.connect(self._add_camera)
        self.edit_btn.clicked.connect(self._edit_camera)
        self.del_btn.clicked.connect(self._delete_camera)
        self.camera_combo.currentIndexChanged.connect(self._on_combo)
        self.stream_combo.currentIndexChanged.connect(self._on_stream_changed)
        self.reset_target_btn.clicked.connect(self._reset_target)
        self.skeleton_check.toggled.connect(self._on_skeleton)
        self.preview.targetClicked.connect(lambda _id: self._update_status())
        self.follow_panel.followRequested.connect(self._on_follow_requested)
        self.follow_panel.compositionChanged.connect(self._on_composition)
        self.follow_panel.effortChanged.connect(self._on_effort)
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._sync_follow)
        self._follow_timer.start(100)
        self._follow_shown = (False, None)

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
        self.preview.set_analyzer(None)
        self.preview.set_follow(None)
        self.controls.bind(None)
        self.edit_btn.setEnabled(cam is not None and cam.kind != "sim")
        self.del_btn.setEnabled(cam is not None and cam.kind != "sim")
        self.stream_combo.blockSignals(True)
        self.stream_combo.setCurrentIndex(max(0, self.stream_combo.findData(cam.stream if cam else "auto")))
        self.stream_combo.blockSignals(False)
        self.stream_combo.setEnabled(cam is not None and cam.kind != "sim")
        if cam is None:
            return
        self.session = CameraSession(cam, scene_path=self.cfg.sim_scene_path, app_cfg=self.cfg)
        self.session.start()
        self.preview.set_source(self.session.frames)
        self.preview.set_analyzer(self.session.analyzer)
        self.preview.set_follow(self.session.follower)
        self.follow_panel.set_composition(cam.composition)
        self.follow_panel.set_effort(cam.effort)
        self.follow_panel.set_calibrated(cam.calibration is not None)
        self.follow_panel.set_state(False, None)
        self._follow_shown = (False, None)
        self.controls.bind(self.session)
        if cam.kind != "sim":
            self.cfg.last_camera_id = cam.id
            self._save()

    # --- слежение -----------------------------------------------------

    def _follower(self):
        return self.session.follower if self.session else None

    def _on_follow_requested(self, want: bool) -> None:
        f = self._follower()
        if f is None:
            self.follow_panel.set_state(False, "распознавание недоступно")
            return
        if want:
            if not f.enable():
                self.follow_panel.set_state(False, None)
                self.follow_panel.show_hint("Сначала кликните по человеку на видео.")
        else:
            f.disable("выключено оператором")
        self._sync_follow()

    def toggle_follow(self) -> None:
        f = self._follower()
        self._on_follow_requested(not (f and f.enabled))

    def _sync_follow(self) -> None:
        """Состояние слежения меняется и из других потоков (ручное управление, сбой анализа) — показать."""
        f = self._follower()
        state = (bool(f and f.enabled), f.last_reason if f else None)
        if state != self._follow_shown:
            self._follow_shown = state
            self.follow_panel.set_state(*state)

    def _on_composition(self, key: str) -> None:
        cam = self._current_cfg()
        self.follow_panel.set_composition(key)
        if cam is None:
            return
        cam.composition = key
        if cam.kind != "sim":
            self._save()
        if f := self._follower():
            f.set_composition(COMPOSITIONS[key])

    def _on_effort(self, x: float) -> None:
        cam = self._current_cfg()
        if cam is None:
            return
        cam.effort = x
        if cam.kind != "sim":
            self._save()
        if f := self._follower():
            f.set_effort(EffortProfile.at(x))

    def _reset_target(self) -> None:
        if self.session and self.session.analyzer:
            self.session.analyzer.clear_target()
        self._update_status()

    def _on_skeleton(self, on: bool) -> None:
        self.preview.show_skeleton = on
        self.preview.update()

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

    def _run_calibration(self) -> None:
        from ptztracker.control.calibration import CalibrationRunner
        from ptztracker.ui.calibration_dialog import CalibrationDialog

        cam, s = self._current_cfg(), self.session
        if cam is None or s is None:
            return
        if s.follower:
            s.follower.disable("калибровка")
        kind = s.stream_kind()

        def factory(progress):
            return CalibrationRunner(s.client.send, s.frames, progress, stream_kind=kind)

        def saved(calib: dict) -> None:
            cam.calibration = calib
            if cam.kind != "sim":
                self._save()

        CalibrationDialog(factory, saved, parent=self).exec()
        if cam.calibration is not None:
            self._activate(cam)  # слежение подхватит новую калибровку

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

    # --- запись (чёрный ящик) ------------------------------------------

    def save_moment(self) -> None:
        if self.session is None:
            self.statusBar().showMessage("Нечего сохранять: камера не выбрана.", 5000)
            return
        self.statusBar().showMessage("Сохраняю запись…")
        fut = self.session.save_moment("F9")
        fut.add_done_callback(self._saveFinished.emit)  # рабочий поток → сигнал → Qt-поток

    def _on_save_finished(self, fut) -> None:
        try:
            path = fut.result()
        except Exception as e:
            log.warning("Запись не сохранена: %s", e)
            self.statusBar().showMessage(f"Запись не сохранена: {e}", 10000)
            return
        self._saved_path = Path(path)
        self.statusBar().showMessage(f"Запись сохранена: {self._saved_path.name}", 10000)
        self.show_folder_btn.show()
        self._hide_folder_timer.start(10000)

    def _show_saved_folder(self) -> None:
        if self._saved_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._saved_path.parent)))

    def _choose_replay(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ptztracker.config import record_dir

        path, _ = QFileDialog.getOpenFileName(self, "Открыть запись", str(record_dir(self.cfg)), "Записи (*.zip)")
        if path:
            self.open_replay(Path(path))

    def open_replay(self, path: Path) -> QWidget | None:
        """Окно просмотра записи; камера и слежение не затрагиваются."""
        from ptztracker.recorder.reader import Episode
        from ptztracker.ui.replay_window import ReplayWindow

        try:
            # отдельное окно: клавиши в нём не управляют камерой (фильтр главного окна его не считает своим)
            w = ReplayWindow(Episode.open(path), parent=self, on_escape=self.emergency_stop)
        except Exception as e:
            QMessageBox.warning(self, "Запись", f"Не удалось открыть запись {path.name}: {e}")
            return None
        w.setAttribute(Qt.WA_DeleteOnClose)
        w.show()
        return w

    def _recording_settings(self) -> None:
        from ptztracker.ui.recording_dialog import RecordingDialog

        d = RecordingDialog(self.cfg, parent=self)
        if d.exec():
            d.apply()
            self._save()

    # --- статус -------------------------------------------------------

    def _update_status(self) -> None:
        s = self.session
        if not s:
            self.link_label.setText("Камера не выбрана")
            self.video_label.setText("")
            self.record_label.setText("")
            return
        self.record_label.setText(
            f"● запись {s.app_cfg.record_buffer_s // 60} мин" if s.recorder.active else ""
        )
        self._remember_rtsp_url(s)
        self.link_label.setText("● на связи" if s.online else "○ нет связи с камерой")
        self.link_label.setStyleSheet("color: #22c55e;" if s.online else "color: #ef4444;")
        st = s.frames.stats()
        if st.connected:
            self.video_label.setText(f"видео {st.width}×{st.height}, {st.fps:.0f} к/с")
        else:
            self.video_label.setText(st.error or "нет видео")
        self._update_analysis_status(s)

    def _update_analysis_status(self, s: CameraSession) -> None:
        a = s.analyzer
        r = a.latest() if a else None
        if a is None:
            self.analysis_label.setText("")
        elif a.error:
            self.analysis_label.setText(f"Распознавание недоступно: {a.error}")
        elif r is None:
            self.analysis_label.setText("анализ: загрузка модели…")
        else:
            self.analysis_label.setText(f"анализ {r.fps:.0f} к/с · {r.infer_ms:.0f} мс · {r.device}")
        if r is None or r.target_id is None:
            self.target_label.setText("Цель: не выбрана — кликните по человеку на видео")
            self.target_label.setStyleSheet("")
        elif r.target_state == "tracking":
            self.target_label.setText(f"Цель: №{r.target_id} — веду")
            self.target_label.setStyleSheet("color: #60a5fa; font-weight: bold;")
        else:
            self.target_label.setText(f"Цель: №{r.target_id} — потеряна")
            self.target_label.setStyleSheet("color: #f59e0b; font-weight: bold;")

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
            if key == Qt.Key_Space:
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self.toggle_follow()
                return True
            comp_keys = {Qt.Key_1 + i: k for i, k in enumerate(COMPOSITIONS)}
            if key in comp_keys:
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self._on_composition(comp_keys[key])
                return True
            if key == Qt.Key_Escape and et == QEvent.KeyPress:
                self.emergency_stop()
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

    def emergency_stop(self) -> None:
        """Esc: отпустить клавиши, выключить слежение, остановить камеру."""
        self.release_all_held()
        if self.session:
            self.session.emergency_stop()

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
        self._hide_folder_timer.stop()
        self._follow_timer.stop()
        self.release_all_held()
        if self.session:
            self.session.stop()
            self.session = None
        self._save()
        super().closeEvent(e)
