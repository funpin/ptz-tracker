"""Служебные клетки: положение, графики, сводка."""

import time

from ptztracker.camera.session import PtzPosition
from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.service_cell import ServiceCell, ServiceRow, card_rects, position_geometry, ptz_text


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
    assert [w.service_kind(i) for i in range(4)] == [None, "summary", "position", "graphs"]
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
    w.set_cell_camera(3, None)  # одна пустая — «Сводка»: положение не нужно
    w._update_status()
    assert not calls
    w.set_cell_camera(2, None)  # вторая пустая — «Положение камер»: опрос нужен
    w._update_status()
    assert calls
    w.close()


def _placed(w):
    return {k: next((i for i, c in enumerate(w.cells) if c._service is wid), None) for k, wid in w._service.items()}


def test_service_widgets_move_when_empties_shift(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.set_cell_camera(0, None)  # пустые [0, 2, 3]
    assert _placed(w) == {"summary": 0, "position": 2, "graphs": 3}
    w.set_cell_camera(0, "sim1")  # пустые сдвигаются вперёд: [2, 3]
    assert _placed(w) == {"summary": 2, "position": 3, "graphs": None}
    assert w._service["summary"].isVisible() and w._service["position"].isVisible()
    w.close()


def test_cone_width_follows_zoom():
    wide = position_geometry(row(PtzPosition(0.0, 0.0, 0.0, 1.0)))
    tele = position_geometry(row(PtzPosition(0.0, 0.0, 0.0, 62.1 / 3.3)))
    assert abs(wide.half_fov - 31.05) < 0.1
    assert abs(tele.half_fov - 1.65) < 0.05
    assert position_geometry(row(PtzPosition(0.0, 0.0, 0.0, None))).half_fov == wide.half_fov


def test_near_limit():
    assert position_geometry(row(PtzPosition(0.0, 165.0, 0.0, 1.0))).near_limit
    assert position_geometry(row(PtzPosition(0.0, -165.0, 0.0, 1.0))).near_limit
    assert not position_geometry(row(PtzPosition(0.0, 100.0, 0.0, 1.0))).near_limit


def test_position_without_ptz(qtbot):
    w = ServiceCell("position")
    qtbot.addWidget(w)
    w.resize(600, 300)
    w.update_view([row()], 0)
    assert not position_geometry(row()).has_data
    assert "нет данных" in w.card_lines(0)
    w.grab()


def test_card_layout():
    from PySide6.QtCore import QRectF

    rect = QRectF(0, 0, 600, 300)
    two = card_rects(2, rect)
    assert len({r.top() for r in two}) == 1
    four = card_rects(4, rect)
    assert len({r.top() for r in four}) == 2 and len({r.left() for r in four}) == 2
    assert all(rect.contains(r) for r in four)


def test_summary_lines(qtbot):
    w = ServiceCell("summary")
    qtbot.addWidget(w)
    w.resize(640, 300)
    a = ServiceRow(0, "Сцена", None, [], 15.0, True, [0], tally="pgm", track="on", battery={0: (83, False)},
                   calibrated=True, video_fps=25.0)
    b = ServiceRow(1, "Зал", None, [], None, True, [1], tally="pvw", track="no_analysis", battery={1: (18, False)},
                   calibrated=False, video_fps=12.0)
    w.update_view([a, b], 0)
    text = " | ".join(w.summary_lines())
    assert "ВЕДУ" in text and "нет калибровки" in text and "1 · Сцена" in text and "PGM" in text
    w.grab()


def test_two_cameras_get_summary_and_position(qtbot, tmp_path):
    w = make(qtbot, tmp_path)  # симулятор открывает две камеры
    assert [w.service_kind(i) for i in range(4)] == [None, None, "summary", "position"]
    assert w.cells[2].combo.itemText(0) == "Сводка" and w.cells[3].combo.itemText(0) == "Положение камер"
    w.close()


def test_no_cameras_shows_welcome(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.set_cell_camera(0, None)
    w.set_cell_camera(1, None)
    w._update_status()
    assert w.welcome.isVisible()
    assert w.cells[0].combo.itemText(0) == "нет камеры"
    w.set_cell_camera(0, "sim1")
    w._update_status()
    assert not w.welcome.isVisible()
    w.close()


def test_summary_gets_window_data(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: bool(w._service["summary"]._rows), timeout=3000)
    rows = w._service["summary"]._rows
    assert rows[0].track in ("off", "on", "lost") and rows[1].track == "no_analysis"
    assert rows[0].video_fps is not None or rows[0].video_fps is None  # поле есть
    w.close()


def test_summary_footer_line(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w._update_status()
    foot = w._service["summary"].footer
    assert "журнал" in foot and "агент" in foot and "ATEM" in foot
    w.close()


def test_target_label_short(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w._update_status()
    assert w.cells[0].target_label.text() == "Цель не выбрана"
    w.close()


def _picture_h(cell) -> int:
    vf = cell.video_frame
    return vf.height() - 2 * vf.BORDER


def _picture_w(cell) -> int:
    vf = cell.video_frame
    return vf.width() - 2 * vf.BORDER


def test_top_row_fits_frame_when_bottom_is_service(qtbot, tmp_path):
    """Внизу нет камер: верхний ряд ровно по кадру 16:9 (без чёрных полос), остальное — служебным клеткам."""
    w = make(qtbot, tmp_path)
    for size in ((1640, 850), (1920, 1001)):
        w.resize(*size)
        qtbot.wait(300)
        top = w.cells[0]
        assert abs(_picture_h(top) - _picture_w(top) * 9 / 16) <= 2, size
        assert w.cells[2].height() > top.height() * 0.6, size  # «Положение камер» остаётся читаемым
    w.close()


def test_rows_equal_with_cameras_below(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.resize(1640, 850)
    w.set_cell_camera(2, "sim3")
    qtbot.wait(300)
    assert abs(w.cells[0].height() - w.cells[2].height()) <= 1
    w.set_cell_camera(2, None)
    qtbot.wait(300)
    top = w.cells[0]
    assert abs(_picture_h(top) - _picture_w(top) * 9 / 16) <= 2
    w.close()


def test_free_cell_choice_and_rotation(qtbot, tmp_path, monkeypatch):
    """Три камеры — одна свободная клетка: в её списке «Сводка», «Положение камер», «По очереди · 30 с»
    (пользователь 2026-11-05). По очереди — смена раз в 30 с, пока мышь над клеткой — пауза."""
    w = make(qtbot, tmp_path)
    w.set_cell_camera(2, "sim3")
    assert w.service_kind(3) == "summary"
    datas = [w.cells[3].combo.itemData(i) for i in range(w.cells[3].combo.count())]
    assert {"svc:summary", "svc:position", "svc:cycle"} <= set(datas)
    assert not any(str(d).startswith("svc:") for d in
                   (w.cells[0].combo.itemData(i) for i in range(w.cells[0].combo.count())))
    w.cells[3].cameraChosen.emit(3, "svc:position")
    assert w.service_kind(3) == "position" and w.grid.cells[3] is None
    w.cells[3].cameraChosen.emit(3, "svc:cycle")
    first = w.service_kind(3)
    w._cycle_tick(force=True)
    assert w.service_kind(3) != first and w.service_kind(3) in ("summary", "position")
    monkeypatch.setattr(w.cells[3], "underMouse", lambda: True)
    cur = w.service_kind(3)
    w._cycle_tick(force=True)
    assert w.service_kind(3) == cur  # мышь над клеткой — смена на паузе
    w.close()


def test_two_free_cells_never_show_same_view(qtbot, tmp_path):
    w = make(qtbot, tmp_path)  # sim1, sim2; свободны 2 и 3
    w.cells[3].cameraChosen.emit(3, "svc:summary")
    kinds = {w.service_kind(2), w.service_kind(3)}
    assert w.service_kind(3) == "summary" and len(kinds) == 2
    w.close()
