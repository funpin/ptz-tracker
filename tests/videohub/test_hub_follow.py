from ptztracker.videohub.client import HubSnapshot
from ptztracker.videohub.follow import HubFollower
from ptztracker.videohub.protocol import HubState

ON = HubSnapshot(True, HubState(present=True, inputs=20, outputs=20, locks={5: "U"}))


def first(f: HubFollower, cam="a"):
    r = f.selected(cam, 1, 5, True, ON)
    assert (r.action, r.reason) == ("skip", "first")


def test_first_report_only_remembered():
    first(HubFollower())


def test_change_routes():
    f = HubFollower()
    first(f)
    r = f.selected("b", 4, 5, True, ON)
    assert (r.action, r.output, r.input) == ("route", 5, 4)


def test_same_camera_skips():
    f = HubFollower()
    first(f)
    assert f.selected("a", 2, 5, True, ON).reason == "same"


def test_skip_reasons():
    cases = [
        (dict(enabled=False), "off"),
        (dict(output=0), "no_output"),
        (dict(hub_input=0), "no_input"),
        (dict(snap=HubSnapshot()), "offline"),
        (dict(snap=HubSnapshot(True, HubState(present=False))), "offline"),
        (dict(snap=HubSnapshot(True, HubState(present=True, locks={5: "L"}))), "locked"),
    ]
    for kw, reason in cases:
        f = HubFollower()
        first(f)
        args = dict(camera_id="b", hub_input=4, output=5, enabled=True, snap=ON) | kw
        r = f.selected(**args)
        assert (r.action, r.reason) == ("skip", reason), kw
        # причина пропуска не «откладывает» команду: тот же выбор снова — уже «same»
        assert f.selected(**args).reason == "same"


def test_own_lock_routes():
    f = HubFollower()
    first(f)
    snap = HubSnapshot(True, HubState(present=True, locks={5: "O"}))
    assert f.selected("b", 4, 5, True, snap).action == "route"


def test_empty_cell_remembered_without_route():
    f = HubFollower()
    first(f)
    assert f.selected(None, 0, 5, True, ON).reason == "no_input"
    assert f.selected("a", 1, 5, True, ON).action == "route"


def test_reset_makes_next_report_first():
    f = HubFollower()
    first(f)
    f.reset()
    assert f.selected("b", 4, 5, True, ON).reason == "first"
