import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent

from ptztracker.config import ConfigStore
from ptztracker.control.calibration import CalibrationCancelled
from ptztracker.control.camera_model import default_calibration
from ptztracker.ui.calibration_dialog import CalibrationDialog
from ptztracker.ui.main_window import MainWindow


class StubRunner:
    def __init__(self, progress):
        self.progress = progress

    def run(self):
        self.progress("Зум 555: скорость 60…")
        return default_calibration()


class FakeRunner:
    """Мастер, который крутится, пока не отменят, и шлёт замеры наблюдателю (как настоящий — из рабочего потока)."""

    def __init__(self, progress, cancel, on_sample, script=None):
        self.progress, self.cancel, self.on_sample = progress, cancel, on_sample
        self.script = script or []
        self.started = threading.Event()
        self.ended = threading.Event()

    def run(self):
        self.started.set()
        try:
            t0 = time.monotonic()
            for kind, data in self.script:
                self.on_sample(kind, data)
            while not self.cancel.is_set():
                t = time.monotonic() - t0
                self.on_sample("apc", {"t": t, "pan": 0x8000 + int(t * 100), "tilt": 0x5555, "zoom": 0x555})
                time.sleep(0.01)
            time.sleep(0.2)  # камера возвращается на место
            raise CalibrationCancelled()
        finally:
            self.ended.set()


def make_dialog(qtbot, script=None):
    runners, saved, events = [], [], []

    def factory(progress, cancel, on_sample):
        events.append(cancel)
        r = FakeRunner(progress, cancel, on_sample, script)
        runners.append(r)
        return r

    d = CalibrationDialog(factory, saved.append)
    qtbot.addWidget(d)
    d.off_air_check.setChecked(True)
    d.start_button.click()
    qtbot.waitUntil(lambda: bool(runners) and runners[0].started.is_set(), timeout=3000)
    return d, runners[0], events[0], saved


def test_dialog_requires_off_air_and_saves(qtbot):
    saved = []
    d = CalibrationDialog(lambda progress, cancel, on_sample: StubRunner(progress), saved.append)
    qtbot.addWidget(d)
    assert not d.start_button.isEnabled()
    d.off_air_check.setChecked(True)
    assert d.start_button.isEnabled()
    d.start_button.click()
    qtbot.waitUntil(lambda: bool(saved), timeout=5000)
    assert "pan" in saved[0] and "Готово" in d.result_label.text()


def test_cancel_button_enabled_whole_run_and_saves_nothing(qtbot):
    d, runner, cancel, saved = make_dialog(qtbot)
    assert d.cancel_button.isEnabled() and not cancel.is_set()
    assert not d.close_button.isEnabled()
    d.cancel_button.click()
    assert cancel.is_set()
    assert "Останавливаю камеру и возвращаю на место" in d.result_label.text()
    assert not d.cancel_button.isEnabled()
    qtbot.waitUntil(lambda: runner.ended.is_set(), timeout=3000)
    qtbot.waitUntil(lambda: "прервана" in d.result_label.text(), timeout=3000)
    assert saved == []
    assert "не сохран" in d.result_label.text()
    assert d.close_button.isEnabled() and not d.cancel_button.isEnabled()


def test_closing_window_cancels_and_closes_when_runner_ends(qtbot):
    d, runner, cancel, saved = make_dialog(qtbot)
    d.show()
    d.close()
    assert cancel.is_set()
    assert d.isVisible()  # камера ещё возвращается: окно не бросает мастер посреди пробега
    qtbot.waitUntil(lambda: not d.isVisible(), timeout=3000)
    assert runner.ended.is_set() and saved == []


def test_escape_cancels(qtbot):
    d, runner, cancel, saved = make_dialog(qtbot)
    d.show()
    qtbot.keyClick(d, Qt.Key_Escape)
    assert cancel.is_set()
    qtbot.waitUntil(lambda: not d.isVisible(), timeout=3000)
    assert saved == []


def test_close_event_ignored_while_running(qtbot):
    d, runner, cancel, _ = make_dialog(qtbot)
    d.show()
    ev = QCloseEvent()
    d.closeEvent(ev)
    assert not ev.isAccepted() and cancel.is_set()
    qtbot.waitUntil(lambda: runner.ended.is_set(), timeout=3000)
    qtbot.waitUntil(lambda: not d.isVisible(), timeout=3000)


def test_samples_from_other_thread_paint_graph(qtbot):
    d, runner, cancel, _ = make_dialog(qtbot)
    d.show()
    errors = []

    def feed():
        try:
            t0 = time.monotonic()
            for i in range(300):
                t = time.monotonic() - t0
                runner.on_sample("apc", {"t": t, "pan": 0x8000 + i, "tilt": 0x5000 - i, "zoom": 0x555 + i})
                if i % 20 == 0:
                    runner.on_sample("cmd", {"t": t, "cmd": "#PTS7050"})
                time.sleep(0.002)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    th = threading.Thread(target=feed)
    th.start()
    for _ in range(10):
        qtbot.wait(40)
        d.graph.grab()  # рисуется на лету, без исключений
    th.join()
    assert not errors
    qtbot.waitUntil(lambda: d.graph.sample_count() > 100, timeout=3000)
    img = d.graph.grab().toImage()
    assert not img.isNull()
    d.cancel_button.click()
    qtbot.waitUntil(lambda: runner.ended.is_set(), timeout=3000)


def test_graph_paints_when_empty(qtbot):
    d = CalibrationDialog(lambda progress, cancel, on_sample: StubRunner(progress), lambda c: None)
    qtbot.addWidget(d)
    d.show()
    assert not d.graph.grab().toImage().isNull()


def test_numbers_appear_from_result_events(qtbot):
    script = [
        ("step", {"index": 0, "total": 5, "title": "Разгон и задержка видео"}),
        ("result", {"name": "tau", "value": 0.3}),
        ("result", {"name": "video_latency", "value": 0.142, "stream": "mjpeg"}),
        ("step", {"index": 2, "total": 5, "title": "Поворот на зуме 555"}),
        ("result", {"name": "pan", "zoom": 0x555, "codes": [[53, 5.5, 4.0], [70, 24.0, 21.5]]}),
        ("result", {"name": "dead_time", "value": 0.1}),
        ("result", {"name": "tilt", "codes": [[63, 9.8, 0.85]]}),
        ("result", {"name": "zoom", "points": [[60, 0.35], [75, 0.6]]}),
    ]
    d, runner, cancel, _ = make_dialog(qtbot, script)
    qtbot.waitUntil(lambda: "0,14" in d.numbers_view.toPlainText(), timeout=3000)
    text = d.numbers_view.toPlainText()
    assert "Мёртвое время" in text and "0,10 с" in text
    assert "Разгон τ" in text and "0,30 с" in text
    assert "Задержка видео" in text and "0,14 с" in text
    assert "555" in text and "24,0" in text and "21,5" in text  # измерено и по модели
    assert "Наклон" in text and "9,8" in text
    assert "Шаг 3 из 5: Поворот на зуме 555" in d.step_label.text()
    d.cancel_button.click()
    qtbot.waitUntil(lambda: runner.ended.is_set(), timeout=3000)
    qtbot.waitUntil(lambda: "прервана" in d.result_label.text(), timeout=3000)
    assert "0,14 с" in d.numbers_view.toPlainText()  # итог остаётся на экране после прерывания


def test_summary_stays_after_successful_run(qtbot):
    class Runner:
        def __init__(self, on_sample):
            self.on_sample = on_sample

        def run(self):
            self.on_sample("result", {"name": "tau", "value": 0.25})
            return default_calibration()

    saved = []
    d = CalibrationDialog(lambda progress, cancel, on_sample: Runner(on_sample), saved.append)
    qtbot.addWidget(d)
    d.off_air_check.setChecked(True)
    d.start_button.click()
    qtbot.waitUntil(lambda: bool(saved), timeout=5000)
    assert "0,25 с" in d.numbers_view.toPlainText()


def test_menu_has_calibration(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    assert w.calib_action.text() == "Калибровка камеры…"
    w.close()


def test_main_window_factory_passes_cancel_observer_and_camera_info(qtbot, tmp_path, monkeypatch):
    seen = {}

    class FakeDialog:
        def __init__(self, factory, on_saved, parent=None):
            self.factory = factory

        def exec(self):
            cancel, obs = threading.Event(), lambda k, d: None
            seen["runner"] = self.factory(lambda m: None, cancel, obs)
            seen["cancel"], seen["obs"] = cancel, obs

    monkeypatch.setattr("ptztracker.ui.calibration_dialog.CalibrationDialog", FakeDialog)
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.session._model, w.session._firmware = "AW-HE42", "1.23"  # то, что камера ответила на QID
    w._run_calibration()
    r = seen["runner"]
    assert r.cancel is seen["cancel"] and r.on_sample is seen["obs"]
    assert (r.model, r.firmware) == ("AW-HE42", "1.23")
    w.close()
