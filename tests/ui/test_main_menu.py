"""Меню ☰ по макету docs/design/mockups/menu-camera-diag.html: группы, значки, состояние справа, без подменю."""

from PySide6.QtWidgets import QWidgetAction

from ptztracker.config import ConfigStore
from ptztracker.ui.main_menu import MenuRow
from ptztracker.ui.main_window import MainWindow


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    return w


def rows(w):
    return {a.text().partition("\t")[0]: a.data() for a in w.main_menu_btn.menu().actions()
            if isinstance(a, QWidgetAction) and isinstance(a.data(), MenuRow)}


def test_groups_without_submenus(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    acts = w.main_menu_btn.menu().actions()
    assert all(a.menu() is None for a in acts)
    heads = [a.text() for a in acts if isinstance(a, QWidgetAction) and not isinstance(a.data(), MenuRow)]
    assert heads == ["Камеры", "Подключения", "Журнал для разбора", "Программа"]
    r = rows(w)
    for t in ("Добавить камеру…", "ATEM…", "Геймпады…", "Агент…", "Сохранить последние 5 минут", "Открыть журнал…",
              "Камер с анализом", "Проверять обновления", "Справка и клавиши"):
        assert t in r, t
    assert r["Сохранить последние 5 минут"].key == "F9"
    w.close()


def test_status_chips(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    r = rows(w)
    assert r["ATEM…"].status_text() == ("не настроен", "off")
    assert r["Геймпады…"].status_text() == ("нет", "off")
    assert r["Агент…"].status_text() == ("выключен", "off")
    w.close()


def test_row_click_triggers_action(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    got = []
    w.help_action.triggered.disconnect()
    w.help_action.triggered.connect(lambda: got.append(1))
    rows(w)["Справка и клавиши"].click()
    assert got == [1]
    w.close()


def test_toggle_and_choices_follow_actions(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    r = rows(w)
    assert r["Камер с анализом"].choices == w.max_analyzed_actions
    before = w.check_updates_action.isChecked()
    r["Проверять обновления"].click()
    assert w.check_updates_action.isChecked() != before
    w.close()


def test_update_row_mirrors_update_action(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    proxy = next(a for a in w.main_menu_btn.menu().actions() if isinstance(a, QWidgetAction)
                 and isinstance(a.data(), MenuRow) and a.data().source is w.update_action)
    assert not proxy.isVisible()
    w.update_action.setText("Доступна версия 9.9.9 — открыть страницу")
    w.update_action.setVisible(True)
    assert proxy.isVisible() and "9.9.9" in proxy.text()
    w.close()


def test_status_bar_says_full_auto(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.session is not None, timeout=3000)
    w.session.refused_recently = lambda: True
    w._update_status()
    assert "Full Auto" in w.statusBar().currentMessage()
    w.close()
