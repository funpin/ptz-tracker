"""Своя строка состояния: сообщения не прячут виджеты слева."""

from PySide6.QtWidgets import QLabel, QMainWindow

from ptztracker.ui.status_bar import MessageStatusBar


def make(qtbot):
    w = QMainWindow()
    qtbot.addWidget(w)
    bar = MessageStatusBar()
    w.setStatusBar(bar)
    lead = QLabel("слева")
    bar.addWidget(lead)
    bar.attach_message_label()
    w.resize(800, 100)
    w.show()
    bar.owner_window = w  # qtbot держит окно только слабой ссылкой
    return bar, lead


def test_message_and_timeout(qtbot):
    bar, _ = make(qtbot)
    bar.showMessage("x", 50)
    assert bar.currentMessage() == "x"
    qtbot.waitUntil(lambda: bar.currentMessage() == "", timeout=1000)


def test_lead_widget_stays_visible_during_message(qtbot):
    bar, lead = make(qtbot)
    bar.showMessage("длинное сообщение")
    qtbot.wait(20)
    assert lead.isVisible() and bar.message_label.x() >= lead.geometry().right()


def test_signal_and_clear(qtbot):
    bar, _ = make(qtbot)
    got = []
    bar.messageChanged.connect(got.append)
    bar.showMessage("a")
    bar.clearMessage()
    assert got == ["a", ""] and bar.currentMessage() == ""


def test_new_message_replaces_old_timer(qtbot):
    bar, _ = make(qtbot)
    bar.showMessage("a", 30)
    bar.showMessage("b")
    qtbot.wait(80)
    assert bar.currentMessage() == "b"


def test_error_message_changes_kind(qtbot):
    bar, _ = make(qtbot)
    bar.showMessage("плохо", 0, error=True)
    assert bar.message_label.property("error") is True
    bar.showMessage("хорошо")
    assert bar.message_label.property("error") is False
