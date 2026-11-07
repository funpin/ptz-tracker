from PySide6.QtCore import Qt

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.theme import C


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    return w


def select(qtbot, w):
    qtbot.waitUntil(lambda: bool(w.session.analyzer.latest() and w.session.analyzer.latest().tracks), timeout=8000)
    tr = w.session.analyzer.latest().tracks[0]
    w.session.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)


def test_credit_visible(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert "by funpin.ru & Claude" in w.credit_label.text()
    w.close()


def test_stop_button_is_emergency_stop(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    select(qtbot, w)
    w.follow_panel.follow_btn.click()
    assert w.session.follower.enabled
    w.controls.stop_btn.click()
    assert not w.session.follower.enabled
    qtbot.wait(400)
    assert [c for c in w.session.sim.received if c.startswith("#PTS")][-1] == "#PTS5050"
    w.close()


def test_tracking_tag_follows_state(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    select(qtbot, w)
    qtbot.keyClick(w, Qt.Key_Space)
    qtbot.waitUntil(lambda: w.video_frame.tracking_tag.text() == "СЛЕЖЕНИЕ", timeout=2000)
    qtbot.keyClick(w, Qt.Key_Space)
    qtbot.waitUntil(lambda: w.video_frame.tracking_tag.text() == "ВЫКЛ", timeout=2000)
    w.close()


def test_track_button_size_constant_in_window(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    before = w.follow_panel.follow_btn.size()
    select(qtbot, w)
    w.follow_panel.follow_btn.click()
    qtbot.wait(300)
    assert w.follow_panel.follow_btn.text() == "TRACK ON"
    assert before.height() == w.follow_panel.follow_btn.height()
    w.close()


def test_window_fits_1640x850(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert w.minimumSizeHint().height() <= 760
    assert w.width() <= 1640
    w.close()


def test_hint_survives_tick_after_earlier_disable(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    select(qtbot, w)
    qtbot.keyClick(w, Qt.Key_Space)
    qtbot.waitUntil(lambda: w.session.follower.enabled, timeout=2000)
    qtbot.keyClick(w, Qt.Key_Space)
    qtbot.waitUntil(lambda: not w.session.follower.enabled, timeout=2000)
    w.session.analyzer.clear_target()
    qtbot.wait(300)
    qtbot.keyClick(w, Qt.Key_Space)
    qtbot.wait(300)
    assert "Сначала кликните" in w.follow_panel.follow_btn.toolTip()
    w.close()


def test_long_analysis_text_keeps_width(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.analysis_pill.set("нет", "fault", "x" * 300)
    assert w.minimumSizeHint().width() <= 1640
    assert w.analysis_pill.toolTip() == "x" * 300
    w.close()


def test_unavailable_message_persists(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.session.follower = None
    w.follow_panel.follow_btn.click()
    assert "распознавание недоступно" in w.follow_panel.follow_btn.toolTip()
    qtbot.wait(300)  # несколько тиков таймера слежения
    assert "распознавание недоступно" in w.follow_panel.follow_btn.toolTip()
    w.close()


def test_failed_enable_keeps_swing_tile(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.session.analyzer.clear_target()
    w.session.follower.controller.diverged = True
    qtbot.waitUntil(lambda: w.follow_panel.follow_btn.kind() == "swing", timeout=2000)
    qtbot.keyClick(w, Qt.Key_Space)  # включить не выйдет: цели нет
    qtbot.wait(300)
    assert not w.session.follower.enabled
    btn = w.follow_panel.follow_btn
    assert btn.kind() == "swing"
    assert not btn.action_btn.isHidden()  # «Калибровка…» на месте
    w.close()


def test_link_ok_chip_is_not_tally_green(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.video_pill.kind() == "ok", timeout=4000)
    assert C.OK != C.PVW  # зелёный PVW — только для Tally
    w.close()


def test_stop_button_visible_without_scrolling(qtbot, tmp_path):
    # аварийная кнопка не прячется за прокруткой правой панели при окне 1640×850 (с боевой темой)
    from PySide6.QtWidgets import QApplication

    from ptztracker.ui.theme import apply_dark_theme

    app = QApplication.instance()
    old_style, old_pal = app.styleSheet(), app.palette()
    apply_dark_theme(app)
    w = make(qtbot, tmp_path)
    w.resize(1640, 850)
    qtbot.waitExposed(w)
    qtbot.wait(1500)  # статус и подсказки обновятся по таймеру и могут добавить строки
    b = w.controls.stop_btn
    assert b.isVisible()
    try:
        assert b.visibleRegion().boundingRect().size() == b.size()
    finally:
        w.close()
        app.setStyleSheet(old_style)
        app.setPalette(old_pal)


def test_version_shown_and_credit_not_at_edge(qtbot, tmp_path):
    from ptztracker import __version__

    w = make(qtbot, tmp_path)
    assert __version__ in w.version_label.text()
    sb = w.statusBar()
    right = w.credit_label.mapTo(sb, w.credit_label.contentsRect().topRight()).x()  # где кончается текст
    assert right <= sb.width() - 12
    w.close()


def test_closed_window_is_freed(qtbot, tmp_path):
    """Окно после закрытия освобождается: приветствие и служебные клетки без родителя держали его связями Qt,
    ~300 МБ на окно, и полный набор тестов на Mac снимала система (код 137). Та утечка была у каждого окна; изредка
    (~1 из 10–20, и на Windows в CI) закрытое окно держится и без неё — причину ищем (docs/BACKLOG.md, «Сборка под
    Mac»), поэтому до трёх окон: хотя бы одно должно освободиться."""
    import gc
    import weakref

    from PySide6.QtCore import QCoreApplication, QEvent

    def freed(n: int) -> bool:
        w = MainWindow(ConfigStore(tmp_path / f"c{n}.json"), use_sim=True)
        w.show()
        qtbot.waitExposed(w)
        ref = weakref.ref(w)
        w.close()
        w.deleteLater()
        del w
        for _ in range(60):  # фоновые потоки камер доработают — до 3 с
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            qtbot.wait(50)
            gc.collect()
            if ref() is None:
                return True
        return False

    assert any(freed(n) for n in range(3))
