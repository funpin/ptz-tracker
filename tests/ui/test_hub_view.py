from ptztracker.ui.hub_view import hub_menu_status, hub_pill
from ptztracker.videohub.client import HubSnapshot
from ptztracker.videohub.protocol import HubState

CAMS = [("Сцена", 3), ("Зал", 4)]


def snap(**kw):
    st = dict(present=True, model="Smart Videohub 20 x 20", routing={5: 3}, locks={5: "U"},
              input_labels={3: "PTZ 1", 7: "Графика"})
    err = kw.pop("last_error", None)
    return HubSnapshot(True, HubState(**(st | kw)), err)


def test_not_configured_no_pill():
    assert hub_pill(False, HubSnapshot(), 5, CAMS) is None
    assert hub_menu_status(False, HubSnapshot(), "") == ("не настроен", "off")


def test_offline():
    assert hub_pill(True, HubSnapshot(), 5, CAMS)[:2] == ("Хаб: нет связи", "fault")
    assert hub_menu_status(True, HubSnapshot(), "10.10.10.50") == ("нет связи", "fault")


def test_ok_with_camera_name():
    assert hub_pill(True, snap(), 5, CAMS)[:2] == ("Хаб: вых. 5 ← Сцена", "ok")
    assert hub_menu_status(True, snap(), "10.10.10.50") == ("на связи · 10.10.10.50", "ok")


def test_foreign_input_shows_label():
    assert hub_pill(True, snap(routing={5: 7}), 5, CAMS)[0] == "Хаб: вых. 5 ← вход 7 · Графика"
    assert hub_pill(True, snap(routing={5: 9}), 5, CAMS)[0] == "Хаб: вых. 5 ← вход 9"


def test_locked_and_nak_and_no_output_and_no_device():
    assert hub_pill(True, snap(locks={5: "L"}), 5, CAMS)[:2] == ("Хаб: выход 5 занят", "warn")
    assert hub_pill(True, snap(locks={5: "O"}), 5, CAMS)[1] == "ok"
    assert hub_pill(True, snap(last_error="nak"), 5, CAMS)[1] == "warn"
    assert hub_pill(True, snap(), 0, CAMS)[:2] == ("Хаб: выход не задан", "warn")
    assert hub_pill(True, HubSnapshot(True, HubState(present=False)), 5, CAMS)[1] == "fault"
