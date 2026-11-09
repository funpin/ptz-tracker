import gc
import threading
import weakref

import pytest
from PySide6.QtWidgets import QWidget

from ptztracker.ui.qt_gc import MainThreadGc


class _Node:
    pass


def _make_cycles(n: int) -> weakref.ref:
    last = None
    for _ in range(n):
        a, b = _Node(), _Node()
        a.other, b.other = b, a
        last = a
    return weakref.ref(last)


@pytest.fixture
def gc_runs():
    """Потоки, в которых шла уборка памяти; до установки MainThreadGc уборка включена, как в обычной программе."""
    threads: list[int] = []

    def note(phase, _info):
        if phase == "start":
            threads.append(threading.get_ident())

    gc.enable()
    gc.callbacks.append(note)
    yield threads
    gc.callbacks.remove(note)
    gc.disable()


@pytest.fixture
def guard(qtbot, gc_runs):
    parent = QWidget()
    qtbot.addWidget(parent)
    g = MainThreadGc(parent, interval_ms=20)
    yield g
    g.stop()


def test_background_thread_never_runs_collection(guard, gc_runs):
    t = threading.Thread(target=_make_cycles, args=(20_000,))
    t.start()
    t.join()
    assert t.ident not in gc_runs


def test_timer_frees_cycles_on_the_qt_thread(guard, gc_runs, qtbot):
    ref = _make_cycles(5_000)
    # одна уборка младшего поколения может оставить цикл старшему: ждём столько, сколько нужно
    qtbot.waitUntil(lambda: ref() is None, timeout=3000)
    assert set(gc_runs) == {threading.get_ident()}


def test_works_without_a_reference_from_outside(gc_runs, qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    MainThreadGc(parent, interval_ms=20)
    gc.collect()  # обёртка без внешней ссылки не должна пропасть
    ref = _make_cycles(5_000)
    qtbot.waitUntil(lambda: ref() is None, timeout=3000)


def test_all_generations_get_collected(guard, gc_runs):
    limits = gc.get_threshold()
    collected: list[int] = []

    def note(phase, info):
        if phase == "start":
            collected.append(info["generation"])

    gc.callbacks.append(note)
    try:
        for _ in range((limits[1] + 2) * (limits[2] + 2)):
            _make_cycles(limits[0])
            guard.check()
    finally:
        gc.callbacks.remove(note)
    assert {0, 1, 2} <= set(collected)


def test_stop_restores_automatic_collection(guard):
    assert not gc.isenabled()
    guard.stop()
    assert gc.isenabled()


def test_gui_start_moves_collection_to_qt_thread(qapp, monkeypatch):
    from unittest.mock import Mock

    import PySide6.QtWidgets as widgets

    from ptztracker import __main__ as entry
    from ptztracker.ui import qt_gc, single_instance, theme

    installed = Mock()
    monkeypatch.setattr(widgets, "QApplication", Mock(return_value=qapp, instance=Mock(return_value=qapp)))
    monkeypatch.setattr(qt_gc, "MainThreadGc", installed)
    monkeypatch.setattr(single_instance, "claim", lambda: None)
    monkeypatch.setattr(theme, "install_russian", lambda _: None)
    monkeypatch.setattr(theme, "apply_dark_theme", lambda _: None)
    monkeypatch.setattr("ptztracker.logging_setup.setup_logging", lambda: None)

    assert entry.run_gui(use_sim=True) == 0
    installed.assert_called_once_with(qapp)
