"""Мини-плитки служебных видов в полоске «1 камера»: ничего не пустует, у каждой — что-то читаемое."""

import time

from PySide6.QtGui import QColor

from ptztracker.camera.session import PtzPosition
from ptztracker.ui.camera_strip import StripCard, StripItem, position_text, summary_clock_text
from ptztracker.ui.service_cell import ServiceRow
from ptztracker.ui.theme import C


def row(**kw):
    base = dict(slot=1, name="Зал", ptz=None)
    base.update(kw)
    return ServiceRow(**base)


def test_summary_clock_says_state_when_no_time():
    assert summary_clock_text(row(clock=("off", None))) == "вручную"
    assert summary_clock_text(row(clock=("air", 75.0))) == "01:15"


def test_position_text():
    assert position_text(row(ptz=PtzPosition(0, -12.4, 3.0, 1.5))) == "−12° · ×1,5"
    assert position_text(row()) == "нет данных"


def test_graphs_tile_draws_lines(qtbot):
    now = time.monotonic()
    hist = [PtzPosition(now - 20 + i, -20 + i * 2, 0.0, 1.0 + i / 10) for i in range(20)]
    card = StripCard(StripItem(3, "Графики PTZ", None, None, service="graphs",
                               rows=[row(ptz=hist[-1], history=hist, track="on")]))
    qtbot.addWidget(card)
    img = card.grab().toImage()
    lime = QColor(C.LIME)
    hits = sum(1 for y in range(img.height()) for x in range(img.width())
               if abs(img.pixelColor(x, y).green() - lime.green()) < 20
               and abs(img.pixelColor(x, y).red() - lime.red()) < 20
               and abs(img.pixelColor(x, y).blue() - lime.blue()) < 30)
    assert hits > 20  # лаймовые линии графика есть


def test_recording_camera_has_dot(qtbot, monkeypatch):
    from PySide6.QtGui import QImage

    from ptztracker.ui import camera_strip as cs
    from ptztracker.ui.camera_strip import StripCard, StripItem
    from ptztracker.ui.theme import C

    monkeypatch.setattr(cs.time, "monotonic", lambda: 0.0)
    img = QImage(128, 72, QImage.Format_RGB888)
    img.fill(0)
    off = StripCard(StripItem(0, "Сцена", None, img))
    on = StripCard(StripItem(0, "Сцена", None, img, recording=True))
    for card in (off, on):
        qtbot.addWidget(card)
        card.resize(128, 72)
    x, y = on.rec_dot_center(on.width())
    assert on.grab().toImage().pixelColor(x, y).name().upper() == C.REC
    assert off.grab().toImage().pixelColor(x, y).name().upper() != C.REC
