"""Служебные клетки: положение, графики, сводка."""

import time

from ptztracker.camera.session import PtzPosition
from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.service_cell import ServiceCell, ServiceRow, ptz_text


def row(ptz=None, history=()):
    return ServiceRow(0, "A", ptz, list(history), None, True, [])


def test_position_text():
    t = ptz_text(PtzPosition(0.0, 12.4, -8.1, 2.3))
    assert "+012.4°" in t and "-008.1°" in t and "2.3×" in t


def test_no_ptz_shows_dash():
    assert ptz_text(None) == "P —  T —  Z —"
    assert ptz_text(PtzPosition(0.0, 1.0, 2.0, None)).endswith("Z —")


def test_graphs_paint_without_history(qtbot):
    for kind in ("position", "graphs", "summary"):
        w = ServiceCell(kind)
        qtbot.addWidget(w)
        w.resize(300, 200)
        w.update_view([row()], 0)
        w.grab()
        now = time.monotonic()
        hist = [PtzPosition(now - i, i, -i, 1.0 + i / 10) for i in range(5)]
        w.update_view([row(hist[0], hist)], 0)
        w.grab()
        w.update_view([], None)
        w.grab()


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    return w


def test_kinds_assigned_in_order(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w._open_cell(1, None)
    assert [w.service_kind(i) for i in range(4)] == [None, "position", "graphs", "summary"]
    w.close()


def test_no_polling_when_four_cameras(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.set_cell_camera(2, "sim3")
    w.set_cell_camera(3, "sim4")
    calls = []
    for c in w.grid.cells:
        c.session.poll_position = lambda: calls.append(1)
    w._update_status()
    assert not calls
    w.set_cell_camera(3, None)
    w._update_status()
    assert calls
    w.close()


def _placed(w):
    return {k: next((i for i, c in enumerate(w.cells) if c._service is wid), None) for k, wid in w._service.items()}


def test_service_widgets_move_when_empties_shift(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.set_cell_camera(0, None)  # пустые [0, 2, 3]
    assert _placed(w) == {"position": 0, "graphs": 2, "summary": 3}
    w.set_cell_camera(0, "sim1")  # пустые сдвигаются вперёд: [2, 3]
    assert _placed(w) == {"position": 2, "graphs": 3, "summary": None}
    assert w._service["position"].isVisible() and w._service["graphs"].isVisible()
    w.close()
