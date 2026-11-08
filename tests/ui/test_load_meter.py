"""Показ нагрузки в верхней строке: всего, доля программы, уровни, подсказка."""

from ptztracker.config import ConfigStore
from ptztracker.system_load import LoadSnapshot, Metric
from ptztracker.ui.load_meter import LoadMeter
from ptztracker.ui.main_window import MainWindow


def make(qtbot):
    m = LoadMeter()
    qtbot.addWidget(m)
    return m


def test_total_and_own_texts(qtbot):
    m = make(qtbot)
    m.set_snapshot(LoadSnapshot(Metric(34, 12), Metric(52, 41), Metric(41, 9)))
    assert m.total_text("cpu") == "34 %" and m.own_text("cpu") == "PTZ 12"


def test_unknown_values_are_dashes(qtbot):
    m = make(qtbot)
    m.set_snapshot(LoadSnapshot(Metric(34, 12), Metric(50, None), Metric(None, None)))
    assert m.own_text("gpu") == "PTZ —" and m.total_text("ram") == "—"


def test_levels_follow_thresholds(qtbot):
    m = make(qtbot)
    m.set_snapshot(LoadSnapshot(Metric(86, 1), Metric(96, 1), Metric(50, 1)))
    assert (m.level("cpu"), m.level("gpu"), m.level("ram")) == ("warn", "crit", "")


def test_tooltip_is_a_table_with_details(qtbot):
    m = make(qtbot)
    m.set_snapshot(LoadSnapshot(Metric(34, 12), Metric(52, None), Metric(41, 9, "6,6 из 16 ГБ · PTZ 1,4 ГБ")))
    tip = m.toolTip()
    assert "Всего" in tip and "PTZ Tracker" in tip and "6,6 из 16 ГБ" in tip and "—" in tip


def test_one_line_label(qtbot):
    m = make(qtbot)
    m.set_snapshot(LoadSnapshot(Metric(92, 61), Metric(52, None), Metric(41, 9)))
    assert m.line_text("cpu") == "CPU 92 % · PTZ 61" and m.line_text("gpu") == "GPU 52 % · PTZ —"


def window(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    return w


def test_meter_lives_in_bottom_bar_and_hides_when_narrow(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    assert w.statusBar().isAncestorOf(w.load_meter) and not w._top.isAncestorOf(w.load_meter)
    w.width = lambda: 3000
    w._fit_top_bar()
    assert not w.load_meter.isHidden()
    w.width = lambda: 300
    w._fit_top_bar()
    assert w.load_meter.isHidden()
    w.close()


def test_video_and_analysis_pills_show_only_on_trouble(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.video_pill.kind() == "ok", timeout=5000)
    w._update_selected_status()
    assert w.video_pill.isHidden()
    w.video_pill.set("нет", "fault", "нет видео")
    w._sync_trouble_pills()
    assert not w.video_pill.isHidden()
    w.close()
