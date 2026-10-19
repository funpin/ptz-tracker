from ptztracker.config import ConfigStore
from ptztracker.control.camera_model import default_calibration
from ptztracker.ui.calibration_dialog import CalibrationDialog
from ptztracker.ui.main_window import MainWindow


class StubRunner:
    def __init__(self, progress):
        self.progress = progress

    def run(self):
        self.progress("Зум 555: скорость 60…")
        return default_calibration()


def test_dialog_requires_off_air_and_saves(qtbot):
    saved = []
    d = CalibrationDialog(lambda progress: StubRunner(progress), saved.append)
    qtbot.addWidget(d)
    assert not d.start_button.isEnabled()
    d.off_air_check.setChecked(True)
    assert d.start_button.isEnabled()
    d.start_button.click()
    qtbot.waitUntil(lambda: bool(saved), timeout=5000)
    assert "pan" in saved[0] and "Готово" in d.result_label.text()


def test_menu_has_calibration(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    assert w.calib_action.text() == "Калибровка камеры…"
    w.close()
