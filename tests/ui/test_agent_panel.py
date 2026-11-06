"""Панель «Агент»: строка с двумя флажками на каждую открытую камеру."""

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    return w


def test_row_per_open_cell_and_checks_write_gate(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    p = w.agent_panel
    assert len(p._rows) == 2
    row0, row1 = (p._rows[c.gate] for c in w.grid.cells[:2])
    assert row0.num_label.text() == "1" and row1.num_label.text() == "2" and row1.title.text()
    row1.allow_check.setChecked(True)
    row1.off_air_check.setChecked(True)
    assert w.grid.cells[1].gate.control_allowed and w.grid.cells[1].gate.not_on_air
    assert not w.grid.cells[0].gate.control_allowed
    assert not p.control_allowed  # выбранная — клетка 1
    w.select_slot(1)
    assert p.control_allowed and p.not_on_air and p.allow_check is row1.allow_check
    w.close()


def test_badge_shows_if_any_camera_allowed(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert not w.agent_badge.isVisibleTo(w)
    w.grid.cells[1].gate.set_allowed(True)
    assert w.agent_badge.isVisibleTo(w)
    w.grid.cells[1].gate.revoke()
    qtbot.waitUntil(lambda: not w.agent_badge.isVisibleTo(w), timeout=1000)
    w.close()


def test_closing_cell_removes_row_and_revoke_all(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    p = w.agent_panel
    g1 = w.grid.cells[1].gate
    g1.set_allowed(True)
    w.set_cell_camera(1, None)
    assert len(p._rows) == 1 and not g1.control_allowed
    w.grid.cells[0].gate.set_allowed(True)
    p.revoke_all()
    assert not w.grid.cells[0].gate.control_allowed and not p.allow_check.isChecked()
    w.close()


def test_card_says_what_agent_may_do(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    p = w.agent_panel
    row = p._rows[w.grid.cells[1].gate]
    assert row.state_label.text() == "агенту нельзя"
    row.allow_check.setChecked(True)
    assert row.state_label.text() == "только выключить слежение"
    row.off_air_check.setChecked(True)
    assert row.state_label.text() == "может двигать камеру" and row.live
    w.close()


def test_tally_warning_only_words(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    p = w.agent_panel
    row = p._rows[w.grid.cells[0].gate]
    p.set_tally(0, "pvw")
    assert "PVW" in row.tally_chip.text() and row.tally_chip.isVisibleTo(p)
    row.off_air_check.setChecked(True)  # Tally ничего не блокирует (решение 2026-10-22) — только слова
    assert w.grid.cells[0].gate.not_on_air and "микшер" in row.warn_label.text()
    p.set_tally(0, "pgm")
    assert "В эфире" in row.warn_label.text()
    p.set_tally(0, None)
    assert not row.warn_label.isVisibleTo(p)
    w.close()


def test_revoke_all_button_and_api_dot(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    p = w.agent_panel
    w.grid.cells[0].gate.set_allowed(True)
    p.revoke_button.click()
    assert not w.grid.cells[0].gate.control_allowed
    p.set_api_status("порт 8765 занят", error=True)
    assert p.api_dot.property("error") is True and "занят" in p.api_label.text()
    w.close()
