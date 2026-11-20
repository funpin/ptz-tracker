"""Ошибки, которые на Windows пропадали бесследно: у собранной программы нет консоли, а Qt печатает ошибку
обработчика кнопки только в stderr. Кнопка молча не срабатывала, в журнале — ничего (площадка, 2026-11-20)."""

import subprocess
import sys
import textwrap
import threading

import pytest
from PySide6.QtCore import QObject, Signal

from ptztracker.logging_setup import install_error_hooks


@pytest.fixture
def hooks(monkeypatch):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    install_error_hooks()


def _errors(caplog):
    return [r for r in caplog.records if r.name == "ptztracker.errors"]


@pytest.mark.qt_no_exception_capture
def test_error_in_qt_slot_goes_to_log(qapp, hooks, caplog):
    class Btn(QObject):
        clicked = Signal()

    def broken_handler():
        raise ValueError("сломался обработчик")

    b = Btn()
    b.clicked.connect(broken_handler)
    b.clicked.emit()
    (rec,) = _errors(caplog)
    assert "ValueError: сломался обработчик" in rec.getMessage() + str(rec.exc_info)
    assert rec.exc_info is not None and "broken_handler" in caplog.text


def test_error_in_thread_goes_to_log(hooks, caplog):
    def boom():
        raise RuntimeError("поток упал")

    t = threading.Thread(target=boom, name="gamepads")
    t.start()
    t.join()
    (rec,) = _errors(caplog)
    assert "gamepads" in rec.getMessage() and rec.exc_info[0] is RuntimeError


def test_native_crash_leaves_stacks_in_hang_log(tmp_path):
    """Падение в C (на Windows — access violation) пишет стеки всех потоков в ptztracker-hang.log."""
    path = tmp_path / "ptztracker-hang.log"
    code = textwrap.dedent(f"""
        import ctypes, os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from ptztracker.ui.hang_watch import HangWatch
        app = QApplication([])
        HangWatch({str(path)!r}, app).start()
        def crash_here():
            ctypes.string_at(0)
        crash_here()
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60)
    assert r.returncode != 0
    text = path.read_text(encoding="utf-8", errors="replace")
    assert "=== запуск" in text and "Fatal Python error" in text and "crash_here" in text
