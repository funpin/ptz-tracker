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
