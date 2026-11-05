from PySide6.QtWidgets import QApplication, QDialogButtonBox

from ptztracker.config import AppConfig
from ptztracker.ui.recording_dialog import RecordingDialog
from ptztracker.ui.theme import install_russian


def test_standard_buttons_in_russian(qtbot):
    install_russian(QApplication.instance())
    box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    qtbot.addWidget(box)
    assert box.button(QDialogButtonBox.Cancel).text() == "Отмена"


def test_black_box_dialog_says_it_is_not_video(qtbot):
    d = RecordingDialog(AppConfig())
    qtbot.addWidget(d)
    assert "журнал" in d.windowTitle().lower()
    assert "не запись видео" in d.about_label.text()


# --- вид по макету docs/design/mockups/journal-replay.html -----------------------------------


def test_journal_minutes_buttons_drive_value(qtbot):
    d = RecordingDialog(AppConfig())
    qtbot.addWidget(d)
    assert set(d.minute_buttons) >= {1, 3, 5, 10, 15}
    assert d.minute_buttons[5].isChecked()
    d.minute_buttons[10].click()
    assert d.minutes.value() == 10 and d.minute_buttons[10].isChecked() and not d.minute_buttons[5].isChecked()
    assert d.ok_button.text() == "Сохранить" and d.ok_button.property("variant") == "primary"


def test_journal_folder_card_counts_records(qtbot, tmp_path):
    (tmp_path / "a.zip").write_bytes(b"x" * 2_000_000)
    (tmp_path / "b.zip").write_bytes(b"x" * 1_000_000)
    (tmp_path / "note.txt").write_text("-")
    cfg = AppConfig()
    cfg.record_dir = str(tmp_path)
    d = RecordingDialog(cfg)
    qtbot.addWidget(d)
    assert d.folder_label.text() == str(tmp_path)
    assert d.folder_stats.text() == "2 записи · 3 МБ"
    d.folder.setText(str(tmp_path / "нет"))
    assert d.folder_stats.text() == "папка будет создана"


def test_journal_desktop_path_is_friendly(qtbot, monkeypatch, tmp_path):
    from ptztracker.ui.recording_dialog import friendly_path

    monkeypatch.setattr("platformdirs.user_desktop_dir", lambda: str(tmp_path / "Desktop"))
    assert friendly_path(tmp_path / "Desktop" / "Записи PTZ Tracker") == "Рабочий стол / Записи PTZ Tracker"


def test_journal_stats_small_and_large(tmp_path, monkeypatch):
    from ptztracker.ui.recording_dialog import folder_stats

    (tmp_path / "a.zip").write_bytes(b"x" * 1000)
    assert folder_stats(tmp_path) == "1 запись · меньше 1 МБ"
