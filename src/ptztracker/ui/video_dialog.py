"""☰ → «Запись видео…»: папка и запас места именно на её диске (макет recording-controls.html)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from ptztracker.config import AppConfig, video_dir
from ptztracker.ui.recording_dialog import friendly_path
from ptztracker.ui.theme import FS_CAPTION, R_PANEL, SP, C
from ptztracker.videorec.controller import format_gb, format_left
from ptztracker.videorec.disk import RATE_CAMERA_BPS, RATE_LIGHT_BPS, DiskInfo, disk_info, is_fat32
from ptztracker.videorec.writer import FAT32_SPLIT_BYTES, file_stem


class _Signals(QObject):
    done = Signal(int, object)


class _DiskTask(QRunnable):
    def __init__(self, token: int, folder: Path, signals: _Signals):
        super().__init__()
        self.token, self.folder, self.signals = token, folder, signals

    def run(self):
        self.signals.done.emit(self.token, disk_info(self.folder))


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
    return label


def _total(n: int) -> str:
    if n < 1_000_000_000_000:
        return f"{format_gb(n)} ГБ"
    return f"{n / 1e12:.1f}".rstrip("0").rstrip(".,").replace(".", ",") + " ТБ"


class VideoDialog(QDialog):
    def __init__(self, cfg: AppConfig, camera_name: str = "Камера", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Запись видео")
        self._cfg = cfg
        self._folder = video_dir(cfg)
        self._initial_folder = self._folder
        self._info: DiskInfo | None = None
        self._token = 0
        self._signals = _Signals()
        self._signals.done.connect(self._on_disk)

        about = QLabel("Видео камеры в файл, отдельно от журнала F9. Запись не влияет на управление и слежение.")
        about.setWordWrap(True)
        about.setStyleSheet(f"color: {C.TEXT_DIM};")

        self.folder_label = QLabel()
        browse = QPushButton("Изменить…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        open_btn = QPushButton("Открыть")
        open_btn.setAutoDefault(False)
        open_btn.clicked.connect(self._open_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_label, 1)
        folder_row.addWidget(browse)
        folder_row.addWidget(open_btn)

        self.disk_title = QLabel()
        self.disk_title.setStyleSheet("font-weight: 600;")
        self.disk_kind = QLabel()
        self.disk_kind.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        self.disk_bar = QProgressBar()
        self.disk_bar.setTextVisible(False)
        self.disk_bar.setFixedHeight(6)
        self.disk_bar.setStyleSheet(
            f"QProgressBar {{ background: {C.RAISED_HI}; border: none; border-radius: 3px; }}"
            f" QProgressBar::chunk {{ background: {C.GREY}; border-radius: 3px; }}")
        self.disk_text = QLabel()
        self.disk_text.setWordWrap(True)
        self.disk_warning = QLabel()
        self.disk_warning.setWordWrap(True)
        self.disk_warning.setStyleSheet(f"color: {C.LOST};")
        self.disk_warning.hide()
        self.disk_card = QFrame()
        self.disk_card.setObjectName("diskCard")
        title_row = QHBoxLayout()
        title_row.addWidget(self.disk_title)
        title_row.addWidget(self.disk_kind)
        title_row.addStretch(1)
        card = QVBoxLayout(self.disk_card)
        card.setContentsMargins(SP[2], SP[2], SP[2], SP[2])
        card.setSpacing(SP[1])
        card.addLayout(title_row)
        card.addWidget(self.disk_bar)
        card.addWidget(self.disk_text)
        card.addWidget(self.disk_warning)

        self.mode_camera = QRadioButton("Как отдаёт камера — H.264 в полном качестве, без пережатия")
        self.mode_light = QRadioButton("Лёгкий файл · 3000 кбит/с, 25p — для архива, нагружает компьютер")
        (self.mode_light if cfg.video_mode == "light" else self.mode_camera).setChecked(True)
        self.mode_camera.toggled.connect(lambda _on: self._render_disk())

        stem = file_stem(camera_name, datetime.now())
        self.example = QLabel(f"{stem}.mp4")
        self.example.setStyleSheet(f"color: {C.TEXT_DIM};")

        cancel = QPushButton("Отмена")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Сохранить")
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.setSpacing(SP[2])
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(ok)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[3], SP[3], SP[3], SP[3])
        lay.setSpacing(SP[2])
        for item in (about, _section("КУДА СОХРАНЯТЬ"), folder_row, self.disk_card, _section("ЧТО ЗАПИСЫВАТЬ"),
                     self.mode_camera, self.mode_light, _section("ИМЯ ФАЙЛА"), self.example, foot):
            (lay.addLayout if isinstance(item, QHBoxLayout) else lay.addWidget)(item)
        self.setMinimumWidth(540)
        self.choose_folder(self._folder, initial=True)

    def _style_card(self, bad: bool) -> None:
        self.disk_card.setProperty("bad", bad)
        border = C.FAULT if bad else C.LINE
        self.disk_card.setStyleSheet(
            f"QFrame#diskCard {{ background: {C.SURFACE}; border: 1px solid {border};"
            f" border-radius: {R_PANEL}px; }} QLabel {{ background: transparent; }}")
        self.disk_text.setStyleSheet(f"color: {C.FAULT if bad else C.TEXT_DIM};")

    def choose_folder(self, path: Path, initial: bool = False) -> None:
        """Сменить папку; диск считается в фоне, ответ для прежней папки отбрасывается."""
        self._folder = Path(path)
        self.folder_label.setText(friendly_path(self._folder))
        self.folder_label.setToolTip(str(self._folder))
        self._token += 1
        QThreadPool.globalInstance().start(_DiskTask(self._token, self._folder, self._signals))

    def _on_disk(self, token: int, info: DiskInfo) -> None:
        if token == self._token:
            self._info = info
            self._render_disk()

    def _rate(self) -> float:
        return RATE_LIGHT_BPS if self.mode_light.isChecked() else RATE_CAMERA_BPS

    def _render_disk(self) -> None:
        info = self._info
        if info is None:
            return
        self._style_card(not info.connected)
        self.disk_bar.setVisible(info.connected)
        self.disk_warning.setVisible(False)
        if not info.connected:
            self.disk_title.setText("")
            self.disk_kind.setText("")
            self.disk_text.setText(f"Диск {info.label} не подключён. "
                                   "Подключите его или выберите другую папку — иначе запись не начнётся.")
            return
        self.disk_title.setText(f"{info.name} ({info.label})" if info.name else info.label)
        kind = {True: "съёмный", False: "внутренний", None: ""}[info.removable]
        self.disk_kind.setText(" · ".join(x for x in (kind, info.fs) if x))
        self.disk_bar.setRange(0, 1000)
        self.disk_bar.setValue(round((info.total - info.free) / max(1, info.total) * 1000))
        minutes = info.free / self._rate() / 60
        self.disk_text.setText(f"Свободно {format_gb(info.free)} ГБ из {_total(info.total)}. Хватит примерно: "
                               f"одна камера — {format_left(minutes)}, четыре — {format_left(minutes / 4)}.")
        if is_fat32(info):
            part = format_left(FAT32_SPLIT_BYTES / self._rate() / 60)
            self.disk_warning.setText("FAT32 не хранит файлы больше 4 ГБ. "
                                      f"Запись сама делится на части примерно по {part}, без пропусков между ними.")
            self.disk_warning.show()

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Папка для записи видео", str(self._folder))
        if d:
            self.choose_folder(Path(d))

    def _open_folder(self) -> None:
        path = self._folder if self._folder.is_dir() else self._folder.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def apply(self) -> None:
        if self._folder != self._initial_folder:  # не трогали — остаётся пусто: «Записи камер» на рабочем столе
            self._cfg.video_dir = str(self._folder)
        self._cfg.video_mode = "light" if self.mode_light.isChecked() else "camera"
