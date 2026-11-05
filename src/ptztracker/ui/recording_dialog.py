from __future__ import annotations

from pathlib import Path

import platformdirs
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ptztracker.config import RECORD_BUFFER_MAX_S, RECORD_BUFFER_MIN_S, AppConfig, record_dir
from ptztracker.ui.icons import icon
from ptztracker.ui.theme import FS_CAPTION, R_PANEL, SP, C

MINUTE_CHOICES = (1, 3, 5, 10, 15)


def friendly_path(path: Path) -> str:
    """«Рабочий стол / Записи PTZ Tracker» вместо полного пути; остальное — как есть."""
    try:
        rel = Path(path).relative_to(Path(platformdirs.user_desktop_dir()))
    except ValueError:
        return str(path)
    return " / ".join(("Рабочий стол", *rel.parts))


def _word(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def folder_stats(path: Path) -> str:
    """«12 записей · 340 МБ» по zip-файлам в папке; папки ещё нет — «папка будет создана»."""
    path = Path(path)
    if not path.is_dir():
        return "папка будет создана"
    try:
        zips = [f for f in path.iterdir() if f.suffix.lower() == ".zip" and f.is_file()]
        size = sum(f.stat().st_size for f in zips)
    except OSError:
        return "папку не прочитать"
    if not zips:
        return "записей пока нет"
    mb = size / 1_000_000
    amount = ("меньше 1 МБ" if mb < 1 else f"{round(mb)} МБ" if mb < 1000
              else f"{mb / 1000:.1f} ГБ".replace(".", ","))
    return f"{len(zips)} {_word(len(zips), 'запись', 'записи', 'записей')} · {amount}"


class RecordingDialog(QDialog):
    """Настройки чёрного ящика по макету docs/design/mockups/journal-replay.html. Меняет cfg только по apply()
    (кнопка «Сохранить»)."""

    def __init__(self, cfg: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Журнал для разбора")
        self._cfg = cfg
        self.about_label = QLabel(
            "Программа всё время помнит последние минуты: маленькие кадры 5 раз в секунду и что делало слежение. "
            f"<b style='color:{C.TEXT}'>F9</b> сохраняет их в zip — его присылают для разбора. "
            "Это не запись видео для эфира.")
        self.about_label.setWordWrap(True)
        self.about_label.setStyleSheet(f"color: {C.TEXT_DIM};")

        self.enabled = QCheckBox("Вести журнал")
        self.enabled.setChecked(cfg.record_enabled)
        # сколько помнить — кнопками; поле minutes остаётся моделью (его читают apply и тесты)
        self.minutes = QSpinBox(self)
        self.minutes.setRange(RECORD_BUFFER_MIN_S // 60, RECORD_BUFFER_MAX_S // 60)
        self.minutes.setValue(cfg.record_buffer_s // 60)
        self.minutes.hide()
        self.minute_buttons: dict[int, QPushButton] = {}
        choices = sorted({*MINUTE_CHOICES, self.minutes.value()})
        seg = QHBoxLayout()
        seg.setSpacing(0)
        for m in choices:
            b = QPushButton(str(m))
            b.setProperty("variant", "seg")
            b.setCheckable(True)
            b.setAutoDefault(False)
            b.setFixedWidth(40)
            b.clicked.connect(lambda _=False, v=m: self.minutes.setValue(v))
            self.minute_buttons[m] = b
            seg.addWidget(b)
        self.minutes.valueChanged.connect(self._sync_minutes)
        self._sync_minutes()
        self.to_disk = QCheckBox("Писать всё на диск")
        self.to_disk.setChecked(cfg.record_to_disk)
        disk_note = QLabel("весь сеанс, около 450 МБ в час")
        disk_note.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px; margin-left: 24px;")

        settings, sl = self._panel()
        sl.addWidget(self._row(self.enabled))
        mins = QHBoxLayout()
        mins.addWidget(QLabel("Помнить последние"))
        mins.addStretch(1)
        mins.addLayout(seg)
        mins.addWidget(QLabel("мин"))
        sl.addWidget(self._row(mins, shaded=True))
        disk = QVBoxLayout()
        disk.setSpacing(2)
        disk.addWidget(self.to_disk)
        disk.addWidget(disk_note)
        sl.addWidget(self._row(disk))

        # папка: понятный путь, сколько там записей и места; поле folder — модель (apply, тесты)
        self._initial_folder = str(record_dir(cfg))
        self.folder = QLineEdit(self._initial_folder, self)
        self.folder.hide()
        self.folder_label = QLabel()
        self.folder_stats = QLabel()
        self.folder_stats.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        open_btn = QPushButton("Открыть")
        open_btn.setAutoDefault(False)
        open_btn.clicked.connect(self._open_folder)
        browse = QPushButton("Изменить…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        ic = QLabel()
        ic.setPixmap(icon("folder", C.TEXT_DIM, 18).pixmap(18, 18))
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(self.folder_label)
        names.addWidget(self.folder_stats)
        frow = QHBoxLayout()
        frow.setSpacing(SP[2])
        frow.addWidget(ic)
        frow.addLayout(names, 1)
        frow.addWidget(open_btn)
        frow.addWidget(browse)
        folder_panel, fl = self._panel()
        fl.addWidget(self._row(frow))
        self.folder.textChanged.connect(self._sync_folder)
        self._sync_folder()

        note = QLabel("Изменения вступят в силу при следующем выборе камеры или перезапуске программы.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        cancel = QPushButton("Отмена")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        self.ok_button = QPushButton("Сохранить")
        self.ok_button.setProperty("variant", "primary")
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.setSpacing(SP[2])
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(self.ok_button)
        where = QLabel("КУДА СОХРАНЯТЬ")
        where.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: 11px; font-weight: 600; letter-spacing: 1px;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[3], SP[3], SP[3], SP[3])
        lay.setSpacing(SP[2])
        lay.addWidget(self.about_label)
        lay.addWidget(settings)
        lay.addWidget(where)
        lay.addWidget(folder_panel)
        lay.addWidget(note)
        lay.addLayout(foot)
        self.setMinimumWidth(540)

    @staticmethod
    def _panel() -> tuple[QFrame, QVBoxLayout]:
        f = QFrame()
        f.setObjectName("journalPanel")
        f.setStyleSheet(f"QFrame#journalPanel {{ background: {C.SURFACE}; border: 1px solid {C.LINE};"
                        f" border-radius: {R_PANEL}px; }} QLabel, QCheckBox {{ background: transparent; }}")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        return f, lay

    @staticmethod
    def _row(content, shaded: bool = False) -> QWidget:
        w = QWidget()
        w.setObjectName("journalRow")
        w.setStyleSheet("QWidget#journalRow { background: %s; }"
                        % ("rgba(245,245,242,0.035)" if shaded else "transparent"))
        h = QHBoxLayout(w)
        h.setContentsMargins(SP[2], SP[1] + 2, SP[2], SP[1] + 2)
        if isinstance(content, QWidget):
            h.addWidget(content)
        else:
            h.addLayout(content)
        return w

    def _sync_minutes(self, *_) -> None:
        for m, b in self.minute_buttons.items():
            b.setChecked(m == self.minutes.value())

    def _sync_folder(self, *_) -> None:
        path = Path(self.folder.text().strip()).expanduser()
        self.folder_label.setText(friendly_path(path))
        self.folder_label.setToolTip(str(path))
        self.folder_stats.setText(folder_stats(path))

    def _open_folder(self) -> None:
        path = Path(self.folder.text().strip()).expanduser()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path if path.is_dir() else path.parent)))

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Папка для записей", self.folder.text())
        if d:
            self.folder.setText(d)

    def apply(self) -> None:
        c = self._cfg
        c.record_enabled = self.enabled.isChecked()
        c.record_buffer_s = self.minutes.value() * 60
        c.record_to_disk = self.to_disk.isChecked()
        text = self.folder.text().strip()
        # не трогали папку — остаётся то, что было (пусто = «Записи PTZ Tracker» на рабочем столе)
        if text != self._initial_folder:
            c.record_dir = text
