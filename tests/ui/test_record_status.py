"""Строка записи внизу слева и её меню."""

from ptztracker.ui.record_status import RecordPill
from ptztracker.videorec.controller import RecRow, RecSummary

ROWS = [RecRow(0, "Сцена", True, True, 754, 10), RecRow(2, "Общий", True, False, 60, 10)]


def make(qtbot):
    p = RecordPill()
    qtbot.addWidget(p)
    return p


def test_hidden_when_nothing_records(qtbot):
    p = make(qtbot)
    p.set_summary(RecSummary())
    assert p.isHidden()


def test_shows_text_and_level(qtbot):
    p = make(qtbot)
    p.set_summary(RecSummary(ROWS, "Запись: 1, 3", "warn", 5))
    assert not p.isHidden() and p.text() == "Запись: 1, 3" and p.property("level") == "warn"
    p.set_summary(RecSummary(ROWS, "Запись: 1, 3", "crit", 5))
    assert p.property("level") == "crit"


def test_menu_has_row_per_camera_and_actions(qtbot):
    p = make(qtbot)
    p.set_summary(RecSummary(ROWS, "x", "", 5))
    stopped, all_, folder = [], [], []
    p.stop_requested.connect(stopped.append)
    p.stop_all_requested.connect(lambda: all_.append(1))
    p.open_folder_requested.connect(lambda: folder.append(1))
    acts = [a for a in p.build_menu().actions() if not a.isSeparator()]
    texts = [a.text() for a in acts]
    assert len(acts) == 4 and "Сцена" in texts[0] and "12:34" in texts[0] and "нет сигнала" in texts[1]
    assert texts[2] == "Остановить все" and texts[3] == "Открыть папку"
    for a in acts:
        a.trigger()
    assert stopped == [0, 2] and all_ == [1] and folder == [1]
