"""Верхняя панель: знак, значки эфира, одно меню ☰ (только общее), TRACK справа; про камеру — в ⋯ клетки."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    return w


def select(qtbot, w):
    qtbot.waitUntil(lambda: bool(w.session.analyzer.latest() and w.session.analyzer.latest().tracks), timeout=8000)
    tr = w.session.analyzer.latest().tracks[0]
    w.session.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)


def test_no_service_menu(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert [a for a in w.menuBar().actions() if a.isVisible()] == []
    texts = [a.text() for a in w.main_menu_btn.menu().actions()]
    assert any("ATEM" in t for t in texts) and any("Агент" in t for t in texts) and "Камеры" in texts  # «Камеры» — подпись группы
    assert not any("Калибровка" in t or "Диагностика" in t for t in texts)  # про одну камеру — в ⋯ клетки
    cell = [a.text() for a in w.cells[0].more_btn.menu().actions()]
    assert any("Калибровка" in t for t in cell) and any("Диагностика" in t for t in cell)
    assert any("ожидания" in t or "Включить" in t for t in cell) and "Видеопоток" in cell
    # значки, как в меню ☰ (ревью 2026-11-06)
    assert all(not a.icon().isNull() for a in w.cells[0].more_btn.menu().actions() if not a.isSeparator())
    w.close()


def test_f9_without_menu(qtbot, tmp_path, monkeypatch):
    w = make(qtbot, tmp_path)
    called = []
    monkeypatch.setattr(w, "save_moment", lambda reason="F9": called.append(reason))
    qtbot.keyClick(w, Qt.Key_F9)
    assert called == ["F9"]
    w.close()


def test_top_bar_texts_short(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.analysis_pill.kind() == "ok", timeout=8000)
    w._update_status()
    for pill in (w.video_pill, w.analysis_pill):
        assert 0 < len(pill.full_text) <= 16, pill.full_text
    assert w.blackbox_btn.full_text == "Журнал"
    assert "лог" in w.blackbox_btn.toolTip().lower()
    w.close()


def test_track_btn_toggles_follow(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert w.track_btn is w.follow_panel.follow_btn
    assert w.track_btn.text() == "TRACK OFF"
    select(qtbot, w)
    w.track_btn.click()
    qtbot.waitUntil(lambda: w.session.follower.enabled, timeout=2000)
    qtbot.waitUntil(lambda: w.track_btn.text() == "TRACK ON", timeout=2000)
    w.track_btn.click()
    qtbot.waitUntil(lambda: w.track_btn.text() == "TRACK OFF", timeout=2000)
    w.close()


def test_agent_badge_follows_flag(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert w.agent_badge.isHidden()
    w.agent_panel.allow_check.setChecked(True)
    qtbot.waitUntil(lambda: not w.agent_badge.isHidden(), timeout=1500)
    w.emergency_stop()
    qtbot.waitUntil(lambda: w.agent_badge.isHidden(), timeout=1500)
    w.close()


def test_agent_window_is_not_modal(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.agent_action.trigger()
    qtbot.waitUntil(lambda: w.agent_panel.isVisible(), timeout=1000)
    assert QApplication.activeModalWidget() is None  # API отвечает 409 только при модальном окне
    w.close()


def test_narrow_window_keeps_everything(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.analysis_pill.kind() == "ok", timeout=8000)
    w.resize(1280, 800)
    qtbot.wait(50)
    w._update_status()
    assert w._top.sizeHint().width() <= w.width()
    assert w.video_pill.text() != "" and w.analysis_pill.text() != ""
    assert w.track_btn.isVisible() and w.track_btn.text() in ("TRACK OFF", "TRACK ON")
    w.close()


def test_compacts_when_bar_too_wide(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.atem_pill.set("3 · PGM", "pgm", "")
    w.atem_pill.show()
    w.blackbox_btn.setMinimumWidth(w.width() - 400)  # крупный шрифт Windows
    w._fit_top_bar()
    assert w.atem_pill.text() == ""
    assert w.track_btn.text() in ("TRACK OFF", "TRACK ON")
    w.close()


def test_tech_status_lives_in_bottom_bar(qtbot, tmp_path):
    # вверху — то, что нужно в эфире; видео и анализ — внизу, рядом с клавишами
    w = make(qtbot, tmp_path)
    sb = w.statusBar()
    for pill in (w.video_pill, w.analysis_pill):
        assert sb.isAncestorOf(pill) and not w._top.isAncestorOf(pill)
    assert w._top.isAncestorOf(w.atem_pill) and w._top.isAncestorOf(w.blackbox_btn)
    w.close()


def test_brand_mark_left(qtbot, tmp_path):
    from ptztracker.ui.widgets import BrandMark

    w = make(qtbot, tmp_path)
    assert w._top.findChildren(BrandMark)
    w.close()


def test_menu_buttons_have_no_arrow():
    from ptztracker.ui.theme import stylesheet

    assert "menu-indicator" in stylesheet()


def test_footer_has_help_not_key_captions(qtbot, tmp_path):
    from PySide6.QtWidgets import QLabel

    w = make(qtbot, tmp_path)
    texts = {lb.text() for lb in w.statusBar().findChildren(QLabel)}
    assert not texts & {"слежение", "стоп", "сохранить момент", "все клавиши"}
    assert w.help_btn.text() == "Справка" and w.help_btn.hotkey == "F1"
    w.help_btn.click()
    assert w.hotkeys_dialog is not None and w.hotkeys_dialog.isVisible()
    w.hotkeys_dialog.close()
    w.close()


def test_stream_menu_switches_stream(qtbot, tmp_path, monkeypatch):
    w = make(qtbot, tmp_path)
    keys = [a.data() for a in w.stream_actions.actions()]
    assert "auto" in keys and "mjpeg_1" in keys
    got = []
    monkeypatch.setattr(w, "_on_stream_changed", lambda key: got.append(key))
    act = next(a for a in w.stream_actions.actions() if a.data() == "mjpeg_1")
    act.setEnabled(True)  # у симулятора поток не выбирается; у живой камеры пункт активен
    act.trigger()
    assert got == ["mjpeg_1"]
    w.close()


def test_cameras_menu(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    texts = [a.text() for a in w.cameras_menu.actions()]
    assert texts[:3] == ["Добавить камеру…", "Изменить камеру…", "Удалить камеру…"]
    assert not w.edit_action.isEnabled()  # симулятор не редактируется
    w.close()


def test_track_squares_follow_analysis(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: [n for n, _k, _s in w.track_btn._cams] == [1], timeout=3000)
    assert w.track_btn._cams[0][2] is True  # выбрана
    w.close()
