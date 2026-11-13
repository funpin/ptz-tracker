"""Настройка области сцены в окне: пункт меню клетки, плашка с кнопками, сохранение. Макет — stage-area.html."""

from PySide6.QtCore import Qt

from ptztracker.config import ConfigStore
from ptztracker.control import stage_area as sa
from ptztracker.ui.main_window import MainWindow

MOVES = ("#PTS", "#Z", "#APS", "#AXZ", "#R", "#F")


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.resize(1400, 820)
    w.show()
    qtbot.waitUntil(lambda: w.preview.has_frame(), timeout=4000)
    return w


def moves(w):
    return [c for c in w.session.sim.received if c.startswith(MOVES)]


def begin(qtbot, w):
    w.stage_action.trigger()
    qtbot.waitUntil(lambda: w.preview.stage_draft is not None, timeout=6000)  # ждёт положение камеры


def test_menu_item_shows_state(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert w.stage_action in w.cells[w.grid.selected].more_btn.menu().actions()
    assert w.stage_action_text() == "Область сцены… — не задана"
    w.session.set_stage_area(sa.StageArea(-10.0, 10.0, -5.0, 5.0))
    assert w.stage_action_text() == "Область сцены… — задана"
    w.session.set_stage_area(sa.StageArea(-10.0, 10.0, -5.0, 5.0, on=False))
    assert w.stage_action_text() == "Область сцены… — выключена"


def test_first_edit_starts_as_line_and_done_saves(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    n = len(moves(w))
    saves, save = [], w._save
    w._save = lambda: (saves.append(1), save())[1]
    begin(qtbot, w)
    d = w.preview.stage_draft
    assert (d.pan0, d.pan1, d.tilt1) == (-180.0, 180.0, 90.0) and w.stage_bar.isVisible()
    qtbot.waitUntil(lambda: "в области" in w.stage_bar.label.text(), timeout=4000)
    qtbot.mouseClick(w.stage_bar.done_btn, Qt.LeftButton)
    assert w.preview.stage_draft is None and not w.stage_bar.isVisible()
    assert w.session.stage_area == d and w.session.follower.stage_on
    assert w.session.cfg.stage_area == d.to_json() and saves == [1]  # в настройках камеры, и они записаны
    assert len(moves(w)) == n, "настройка области камеру не двигает"


def test_cancel_and_escape_restore_previous_area(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    old = sa.StageArea(-10.0, 10.0, -5.0, 5.0)
    w.session.set_stage_area(old)
    begin(qtbot, w)
    assert w.preview.stage_draft == old
    w.preview.stage_draft = sa.StageArea(-30.0, 30.0, -5.0, 5.0)
    qtbot.mouseClick(w.stage_bar.cancel_btn, Qt.LeftButton)
    assert w.session.stage_area == old and w.preview.stage_draft is None
    begin(qtbot, w)
    w.preview.stage_draft = sa.StageArea(-30.0, 30.0, -5.0, 5.0)
    qtbot.keyClick(w, Qt.Key_Escape)  # Esc — стоп всем и отмена правки
    assert w.session.stage_area == old and w.preview.stage_draft is None and not w.stage_bar.isVisible()


def test_switch_off_keeps_geometry(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.session.set_stage_area(sa.StageArea(-10.0, 10.0, -5.0, 5.0))
    begin(qtbot, w)
    qtbot.mouseClick(w.stage_bar.off_btn, Qt.LeftButton)
    assert w.session.stage_area == sa.StageArea(-10.0, 10.0, -5.0, 5.0, on=False)
    assert not w.session.follower.stage_on
    begin(qtbot, w)  # снова открыли — правим ту же рамку, «Готово» включает её
    qtbot.mouseClick(w.stage_bar.done_btn, Qt.LeftButton)
    assert w.session.stage_area == sa.StageArea(-10.0, 10.0, -5.0, 5.0)


def test_edit_does_not_switch_following_off(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    s = w.session
    qtbot.waitUntil(lambda: (r := s.analyzer.latest()) is not None and bool(r.tracks), timeout=5000)
    t = s.analyzer.latest().tracks[0]
    s.analyzer.select_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)
    qtbot.waitUntil(lambda: s.follower.enable(), timeout=3000)
    begin(qtbot, w)
    qtbot.mouseClick(w.stage_bar.done_btn, Qt.LeftButton)
    assert s.follower.enabled
