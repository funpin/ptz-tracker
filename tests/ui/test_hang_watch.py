"""Сторож зависаний (ui.hang_watch): окно не отвечает — в файл рядом с журналом пишется, чем занят каждый поток."""

import ctypes
import logging
import sys
import threading
import time

import pytest

from ptztracker.ui.hang_watch import FILE_NAME, HangWatch, hang_log_path

STALL_S = 1.5  # столько держим поток Qt; порог сторожа в этих тестах 0,3 с — запас на занятую машину CI


@pytest.fixture
def watch(qapp, tmp_path):
    made = []

    def make(**kw):
        w = HangWatch(tmp_path / FILE_NAME, qapp, **kw)
        made.append(w)
        return w

    yield make
    for w in made:
        w.stop()


def text(w) -> str:
    return w.path.read_text(encoding="utf-8", errors="replace")


def stuck_in_qt_thread(seconds):
    time.sleep(seconds)


def stall(w, how=stuck_in_qt_thread) -> int:
    """Зависание при заведомо взведённом стороже; возвращает, сколько записей прибавилось. Такт перед зависанием —
    чтобы на занятой машине случайная запись в ожидании до него не оставила сторожа невзведённым."""
    w.beat()
    before = text(w).count("Timeout")
    how(STALL_S)
    return text(w).count("Timeout") - before


def hold_gil(seconds):
    """Вызов C, который не отпускает GIL, — как SDL, ждущий замок джойстиков (Mac, 2026-11-06)."""
    if sys.platform == "win32":
        ctypes.PyDLL("kernel32").Sleep(int(seconds * 1000))
    else:
        ctypes.PyDLL(None).usleep(int(seconds * 1_000_000))


def test_start_writes_header_and_no_dump_while_window_responds(watch, qtbot):
    from ptztracker import __version__

    w = watch(hang_s=3.0, beat_ms=50)
    assert w.start()
    qtbot.wait(400)
    t = text(w)
    assert "запуск" in t and __version__ in t
    assert "Timeout" not in t  # окно отвечало: стеков нет


def test_stalled_qt_thread_dumps_every_thread(watch, qtbot):
    parked = threading.Event()

    def parked_background_thread():
        parked.wait(10)

    th = threading.Thread(target=parked_background_thread, daemon=True)
    th.start()
    w = watch(hang_s=0.3, beat_ms=50)
    assert w.start()
    qtbot.wait(150)
    try:
        assert stall(w) == 1
    finally:
        parked.set()
    t = text(w)
    assert "stuck_in_qt_thread" in t  # где стоит поток окна
    assert "parked_background_thread" in t  # и чем заняты остальные


def test_dump_is_written_even_when_a_thread_holds_the_gil(watch, qtbot):
    """Фоновый поток застрял в C и держит GIL: поток Qt и любой сторож на Python стоят. Запись всё равно
    появляется, и в ней — тот самый вызов."""
    w = watch(hang_s=0.3, beat_ms=50)
    assert w.start()
    qtbot.wait(150)

    def background_thread_holds_gil(seconds):
        th = threading.Thread(target=hold_gil, args=(seconds,), daemon=True)
        th.start()
        th.join(seconds + 5)

    assert stall(w, background_thread_holds_gil) == 1
    assert "hold_gil" in text(w)


def test_recovery_is_noted_in_the_main_log(watch, qtbot, caplog):
    w = watch(hang_s=0.3, beat_ms=50)
    assert w.start()
    qtbot.wait(150)
    with caplog.at_level(logging.WARNING, logger="ptztracker.ui.hang_watch"):
        stall(w)
        qtbot.waitUntil(lambda: any("не отвечало" in r.getMessage() for r in caplog.records), timeout=3000)
    msg = next(r.getMessage() for r in caplog.records if "не отвечало" in r.getMessage())
    assert FILE_NAME in msg  # где искать стеки


def test_one_dump_per_stall_and_watch_rearms(watch, qtbot):
    w = watch(hang_s=0.3, beat_ms=50)
    assert w.start()
    qtbot.wait(150)
    assert stall(w) == 1  # зависание в 5 раз дольше порога — запись одна
    qtbot.waitUntil(lambda: "окно ожило" in text(w), timeout=3000)  # таймер окна сам заметил и взвёл заново
    assert stall(w) == 1


def test_watch_goes_quiet_when_the_file_is_full(watch, qtbot, monkeypatch):
    """Программа, которая замирает раз за разом, не должна исписать диск: файл полон — сторож молчит до запуска."""
    from ptztracker.ui import hang_watch

    w = watch(hang_s=0.3, beat_ms=50)
    assert w.start()
    monkeypatch.setattr(hang_watch, "MAX_BYTES", 200)  # одна запись уже больше
    qtbot.wait(150)
    stuck_in_qt_thread(STALL_S)
    qtbot.waitUntil(lambda: "файл полон" in text(w), timeout=3000)  # окно ожило: файл больше предела
    before = text(w).count("Timeout")
    stuck_in_qt_thread(STALL_S)
    assert text(w).count("Timeout") == before  # больше не пишет


def test_stop_disarms(watch, qtbot):
    w = watch(hang_s=0.3, beat_ms=50)
    assert w.start()
    w.stop()
    stuck_in_qt_thread(1.0)
    assert "Timeout" not in w.path.read_text(encoding="utf-8")
    w.stop()  # повторная остановка не бросает


def test_start_never_raises_when_file_cannot_be_opened(qapp, tmp_path, caplog):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    w = HangWatch(blocker / FILE_NAME, qapp)
    with caplog.at_level(logging.WARNING, logger="ptztracker.ui.hang_watch"):
        assert w.start() is False
    assert any("Сторож зависаний" in r.getMessage() for r in caplog.records)
    w.stop()


def test_big_file_is_rotated_at_start(watch, tmp_path):
    p = tmp_path / FILE_NAME
    p.write_text("старое\n" * 200_000, encoding="utf-8")  # больше 1 МБ
    w = watch()
    assert w.start()
    assert p.stat().st_size < 10_000
    assert (tmp_path / (FILE_NAME + ".1")).read_text(encoding="utf-8").startswith("старое")


def test_file_lives_next_to_the_main_log(monkeypatch, tmp_path):
    import platformdirs

    monkeypatch.setattr(platformdirs, "user_log_dir", lambda *a, **k: str(tmp_path / "Logs"))
    assert hang_log_path() == tmp_path / "Logs" / FILE_NAME


def test_gui_start_arms_the_watch_after_the_single_instance_check(monkeypatch, caplog):
    from unittest.mock import Mock

    import PySide6.QtWidgets as widgets

    from ptztracker import __main__ as entry
    from ptztracker.ui import hang_watch, main_window, qt_gc, single_instance, theme

    app = Mock()
    app.exec.return_value = 0
    installed = Mock()
    monkeypatch.setattr(widgets, "QApplication", Mock(return_value=app))
    monkeypatch.setattr(qt_gc, "MainThreadGc", Mock())
    monkeypatch.setattr(hang_watch, "HangWatch", installed)
    monkeypatch.setattr(main_window, "MainWindow", Mock())
    monkeypatch.setattr("ptztracker.config.ConfigStore", Mock())
    monkeypatch.setattr(theme, "install_russian", lambda _: None)
    monkeypatch.setattr(theme, "apply_dark_theme", lambda _: None)
    monkeypatch.setattr("ptztracker.logging_setup.setup_logging", lambda: None)

    monkeypatch.setattr(single_instance, "claim", lambda: None)  # вторая копия: файл не трогает
    with caplog.at_level(logging.INFO):
        assert entry.run_gui(use_sim=True) == 0
    installed.assert_not_called()
    # «не открывается» после зависания (площадка 2026-11-13): в журнале видно, что запуск был и почему он вышел
    assert any("уже запущен" in r.getMessage() for r in caplog.records)

    monkeypatch.setattr(single_instance, "claim", lambda: Mock())
    assert entry.run_gui(use_sim=True) == 0
    installed.assert_called_once_with(hang_watch.hang_log_path(), app)
    installed.return_value.start.assert_called_once_with()
