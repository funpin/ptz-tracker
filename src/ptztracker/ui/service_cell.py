"""Служебные клетки сетки: положение камер, графики PTZ, сводка. Только рисуют снимки состояния.
Вид — docs/BRANDBOOK.md и спека 2026-11-05-brand-and-window-redesign-design.md («Пустые клетки»)."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Literal

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ptztracker.camera.he42 import HFOV_TELE_DEG, HFOV_WIDE_DEG, PAN_LIMIT_DEG
from ptztracker.camera.session import PtzPosition
from ptztracker.ui.theme import FONT_FAMILY, FS_CAPTION, R_PANEL, R_TAG, SP, C, qcolor
from ptztracker.ui.widgets import BrandMark, PadBadge, StateTag

Kind = Literal["position", "graphs", "summary"]
KINDS: tuple[Kind, ...] = ("summary", "position", "graphs")  # порядок, в котором занимают пустые клетки
GRAPH_SECONDS = 30.0
TITLES = {"position": "Положение камер", "graphs": "Графики PTZ", "summary": "Сводка"}
GRAPHS_NOTE = "Запись видео — в следующей версии"
NEAR_LIMIT_DEG = 15.0  # ближе к пределу поворота — оранжевая подпись
TILT_RANGE = (-30.0, 90.0)  # шкала наклона на карточке, °
ZOOM_MAX_X = HFOV_WIDE_DEG / HFOV_TELE_DEG


@dataclass(frozen=True)
class ServiceRow:
    slot: int
    name: str
    ptz: PtzPosition | None
    history: list[PtzPosition] = field(default_factory=list)
    analysis_fps: float | None = None
    online: bool = False
    pads: list[int] = field(default_factory=list)
    tally: str | None = None  # "pgm" | "pvw" | None
    track: str = "off"  # on | lost | off | standby | no_analysis
    battery: dict[int, tuple[int | None, bool]] = field(default_factory=dict)  # геймпад → (заряд %, заряжается)
    calibrated: bool = False
    video_fps: float | None = None
    preset_pans: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class PositionGeometry:
    has_data: bool
    pan: float  # °, 0 — прямо, плюс — вправо
    tilt: float
    zoom_x: float
    half_fov: float  # половина угла обзора на текущем зуме, °
    near_limit: bool


def position_geometry(r: ServiceRow) -> PositionGeometry:
    if r.ptz is None:
        return PositionGeometry(False, 0.0, 0.0, 1.0, HFOV_WIDE_DEG / 2, False)
    zx = min(ZOOM_MAX_X, max(1.0, r.ptz.zoom_x or 1.0))
    pan = float(r.ptz.pan_deg)
    return PositionGeometry(True, pan, float(r.ptz.tilt_deg), zx, HFOV_WIDE_DEG / zx / 2,
                            abs(pan) > PAN_LIMIT_DEG - NEAR_LIMIT_DEG)


def card_rects(n: int, rect: QRectF) -> list[QRectF]:
    """1–2 камеры — карточки в ряд, 3–4 — сеткой 2×2."""
    if n <= 0:
        return []
    cols = n if n <= 2 else 2
    rows = 1 if n <= 2 else 2
    gap = SP[2]
    w = (rect.width() - gap * (cols - 1)) / cols
    h = (rect.height() - gap * (rows - 1)) / rows
    return [QRectF(rect.left() + (i % cols) * (w + gap), rect.top() + (i // cols) * (h + gap), w, h)
            for i in range(n)]


def ptz_text(ptz: PtzPosition | None) -> str:
    if ptz is None:
        return "P —  T —  Z —"
    zoom = f"{ptz.zoom_x:.1f}×" if ptz.zoom_x is not None else "—"
    return f"P {ptz.pan_deg:+06.1f}°  T {ptz.tilt_deg:+06.1f}°  Z {zoom}"


class ServiceCell(QWidget):
    def __init__(self, kind: Kind, parent: QWidget | None = None):
        super().__init__(parent)
        self.kind: Kind = kind
        self._rows: list[ServiceRow] = []
        self._selected: int | None = None
        self._tags: dict[str, StateTag] = {}
        self._badge = PadBadge()
        self.setMinimumSize(120, 90)

    def update_view(self, rows: list[ServiceRow], selected: int | None) -> None:
        self._rows = list(rows)
        self._selected = selected
        self.update()

    # --- рисование -----------------------------------------------------

    def _font(self, px: float, bold: bool = False) -> QFont:
        f = QFont(FONT_FAMILY)
        f.setPixelSize(round(px))
        f.setBold(bold)
        return f

    def _tag(self, kind: str) -> StateTag:
        t = self._tags.get(kind)
        if t is None:
            t = self._tags[kind] = StateTag(kind)
            t.adjustSize()
        return t

    def _draw_widget(self, p: QPainter, w: QWidget, x: float, y: float) -> float:
        """Рисует готовый виджет (плашку, значок геймпада) в точке; возвращает его ширину."""
        p.save()
        p.translate(QPointF(x, y))
        w.render(p, QPoint(0, 0))
        p.restore()
        return w.width()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), qcolor(C.SURFACE))
        body = QRectF(SP[2], SP[1], self.width() - 2 * SP[2], self.height() - SP[1] - SP[2])
        if self.kind == "position":
            self._paint_position(p, body)
        elif self.kind == "graphs":
            self._paint_graphs(p, body)
        else:
            self._paint_summary(p, body)
        p.end()

    # --- положение: компас на камеру ------------------------------------------

    def card_lines(self, i: int) -> str:
        r = self._rows[i]
        g = position_geometry(r)
        if not g.has_data:
            return f"{r.slot + 1} · {r.name} · нет данных"
        tail = " · у предела поворота" if g.near_limit else ""
        return (f"{r.slot + 1} · {r.name} · P {g.pan:+.1f}°  T {g.tilt:+.1f}°  "
                f"{g.zoom_x:.1f}× · {2 * g.half_fov:.0f}°{tail}")

    def _paint_position(self, p: QPainter, body: QRectF) -> None:
        for r, card in zip(self._rows, card_rects(len(self._rows), body), strict=False):
            self._paint_card(p, r, card)

    def _paint_card(self, p: QPainter, r: ServiceRow, card: QRectF) -> None:
        g = position_geometry(r)
        sel = r.slot == self._selected
        p.setOpacity(1.0 if g.has_data else 0.5)
        p.setPen(QPen(qcolor(C.TEXT if sel else C.LINE), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), R_PANEL - 2, R_PANEL - 2)
        # шапка карточки: номер, имя, состояние
        hx, hy = card.left() + SP[1] + 2, card.top() + SP[1]
        num = QRectF(hx, hy, 18, 18)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.TEXT if sel else C.RAISED_HI))
        p.drawRoundedRect(num, R_TAG, R_TAG)
        p.setFont(self._font(11, True))
        p.setPen(qcolor(C.BG if sel else C.TEXT))
        p.drawText(num, Qt.AlignCenter, str(r.slot + 1))
        p.setFont(self._font(FS_CAPTION, True))
        p.setPen(qcolor(C.TEXT))
        tag = self._tag(r.track)
        name_w = card.width() - 18 - 3 * SP[1] - tag.width() - SP[1]
        p.drawText(QRectF(num.right() + SP[1] - 2, hy, name_w, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(r.name, Qt.ElideRight, int(name_w)))
        self._draw_widget(p, tag, card.right() - SP[1] - 2 - tag.width(), hy + (18 - tag.height()) / 2)
        # низ: цифры
        vals_h = 16.0
        vy = card.bottom() - SP[1] - vals_h
        p.setFont(self._font(FS_CAPTION - 0.5))
        p.setPen(qcolor(C.TEXT_DIM))
        if g.has_data:
            vals = (f"P {g.pan:+.1f}°", f"T {g.tilt:+.1f}°", f"{g.zoom_x:.1f}× · {2 * g.half_fov:.0f}°")
            third = (card.width() - 2 * SP[1]) / 3
            for k, v in enumerate(vals):
                align = (Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignRight)[k] | Qt.AlignVCenter
                p.drawText(QRectF(card.left() + SP[1] + k * third, vy, third, vals_h), align, v)
        else:
            p.drawText(QRectF(card.left(), vy, card.width(), vals_h), Qt.AlignCenter, "нет данных")
        # середина: компас слева, шкалы наклона и зума справа
        mid = QRectF(card.left() + SP[1], hy + 18 + SP[0], card.width() - 2 * SP[1], vy - hy - 18 - 2 * SP[0])
        gauges_w = 52.0
        side = min(mid.width() - gauges_w, mid.height())
        if side < 30:
            p.setOpacity(1.0)
            return
        dial = QRectF(mid.left() + (mid.width() - gauges_w - side) / 2, mid.top() + (mid.height() - side) / 2,
                      side, side)
        self._paint_dial(p, r, g, dial, sel)
        gx = mid.right() - gauges_w + SP[1]
        tilt_k = (g.tilt - TILT_RANGE[0]) / (TILT_RANGE[1] - TILT_RANGE[0])
        zoom_k = math.log(g.zoom_x) / math.log(ZOOM_MAX_X)
        for k, (label, frac) in enumerate((("наклон", tilt_k), ("зум", zoom_k))):
            self._paint_gauge(p, QRectF(gx + k * 22, mid.top() + 4, 18, mid.height() - 8), label, frac,
                              g.has_data)
        p.setOpacity(1.0)

    def _paint_dial(self, p: QPainter, r: ServiceRow, g: PositionGeometry, box: QRectF, sel: bool) -> None:
        c = box.center()
        rad = box.width() / 2 - 8

        def at(deg: float, rr: float) -> QPointF:
            a = math.radians(deg - 90)  # 0° — вверх, плюс — вправо
            return QPointF(c.x() + rr * math.cos(a), c.y() + rr * math.sin(a))

        arc = QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad)
        p.setPen(QPen(qcolor(C.LINE), 1))
        p.setBrush(Qt.NoBrush)
        # Qt: углы в 1/16°, 0 — вправо, против часовой; наш 0° — вверх, по часовой
        p.drawArc(arc, int((90 - PAN_LIMIT_DEG) * 16), int(2 * PAN_LIMIT_DEG * 16))
        p.setPen(QPen(qcolor(C.FAULT, 150), 2))
        p.drawArc(arc, int((90 - 185) * 16), int(10 * 16))  # предел поворота — внизу
        p.setPen(QPen(qcolor(C.GREY), 1))
        p.drawLine(at(0, rad - 4), at(0, rad + 5))
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.GREY))
        for pan in r.preset_pans:
            p.drawEllipse(at(pan, rad), 2.2, 2.2)
        if g.has_data:
            col = C.LIME if r.track == "on" else C.TEXT
            cone = QPainterPath(c)
            cone.arcTo(arc, 90 - g.pan - g.half_fov, 2 * g.half_fov)
            cone.closeSubpath()
            p.setBrush(qcolor(col, 36))
            p.setPen(QPen(qcolor(col, 180), 1))
            p.drawPath(cone)
            p.setPen(QPen(qcolor(col), 1.5))
            p.drawLine(c, at(g.pan, rad))
            if g.near_limit:
                p.setFont(self._font(10))
                p.setPen(qcolor(C.LOST))
                p.drawText(QRectF(box.left() - 20, box.bottom() - 10, box.width() + 40, 12), Qt.AlignCenter,
                           "у предела поворота")
        p.setPen(QPen(qcolor(C.TEXT if sel else C.GREY), 1.8))
        p.setBrush(qcolor(C.BG))
        p.drawEllipse(c, 10, 10)
        p.setFont(self._font(11, True))
        p.setPen(qcolor(C.TEXT))
        p.drawText(QRectF(c.x() - 10, c.y() - 10, 20, 20), Qt.AlignCenter, str(r.slot + 1))

    def _paint_gauge(self, p: QPainter, box: QRectF, label: str, frac: float, has: bool) -> None:
        rail = QRectF(box.right() - 6, box.top(), 4, box.height())
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.RAISED_HI))
        p.drawRoundedRect(rail, 2, 2)
        if has:
            y = rail.bottom() - max(0.0, min(1.0, frac)) * rail.height()
            p.setBrush(qcolor(C.TEXT))
            p.drawRoundedRect(QRectF(rail.left() - 4, y - 1.5, 12, 3), 1.5, 1.5)
        p.save()
        p.translate(box.left() + 6, box.center().y())
        p.rotate(-90)
        p.setFont(self._font(9.5))
        p.setPen(qcolor(C.TEXT_FAINT))
        p.drawText(QRectF(-box.height() / 2, -6, box.height(), 12), Qt.AlignCenter, label)
        p.restore()

    # --- графики ----------------------------------------------------------------

    def _paint_graphs(self, p: QPainter, body: QRectF) -> None:
        row = next((r for r in self._rows if r.slot == self._selected), None)
        note_h = 16.0
        plot = QRectF(body.left(), body.top(), body.width(), body.height() - note_h - 4)
        p.setPen(QPen(qcolor(C.LINE), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(plot.adjusted(0.5, 0.5, -0.5, -0.5), R_PANEL - 2, R_PANEL - 2)
        if row is not None and row.history and plot.height() > 20:
            now = time.monotonic()
            series = (
                (C.TEXT, [h.pan_deg for h in row.history], "P"),
                (C.LOST, [h.tilt_deg for h in row.history], "T"),
                (C.LIME, [h.zoom_x if h.zoom_x is not None else math.nan for h in row.history], "Z"),
            )
            xs = [1.0 - (now - h.t) / GRAPH_SECONDS for h in row.history]
            for color, ys, label in series:
                good = [y for y in ys if not math.isnan(y)]
                if not good:
                    continue
                lo, hi = min(good), max(good)
                span = (hi - lo) or 1.0
                pts = [
                    QPointF(plot.left() + max(0.0, min(1.0, x)) * plot.width(),
                            plot.bottom() - 4 - (y - lo) / span * (plot.height() - 8))
                    for x, y in zip(xs, ys, strict=True) if not math.isnan(y)
                ]
                p.setPen(QPen(qcolor(color), 1.5))
                if len(pts) > 1:
                    p.drawPolyline(pts)
                p.setFont(self._font(FS_CAPTION))
                p.drawText(QRectF(plot.left() + SP[1], plot.top() + SP[0] + "PTZ".index(label) * 14, 80, 14),
                           Qt.AlignLeft | Qt.AlignVCenter, f"{label} {good[-1]:.1f}")
        p.setPen(qcolor(C.TEXT_FAINT))
        p.setFont(self._font(FS_CAPTION))
        p.drawText(QRectF(body.left(), body.bottom() - note_h, body.width(), note_h),
                   Qt.AlignLeft | Qt.AlignVCenter, GRAPHS_NOTE)

    # --- сводка -----------------------------------------------------------------

    def summary_lines(self) -> list[str]:
        out = []
        for r in self._rows:
            tags = " ".join(StateTag.TEXTS[k] for k in (r.tally, r.track) if k)
            pads = ", ".join(f"геймпад {n} {self._pct_text(r, n)}" for n in r.pads) or "—"
            fps = self._fps_text(r)
            cal = "калибровка" if r.calibrated else "нет калибровки"
            out.append(f"{r.slot + 1} · {r.name} | {tags} | {pads} | {fps} | {cal}")
        return out

    @staticmethod
    def _pct_text(r: ServiceRow, pad: int) -> str:
        pct = r.battery.get(pad, (None, False))[0]
        return "—" if pct is None else f"{pct}%"

    def _paint_summary(self, p: QPainter, body: QRectF) -> None:
        """Строка на камеру; колонки по ширине содержимого: имя | эфир | слежение | геймпады | к/с | калибровка.
        Узко — сначала пропадают к/с, потом геймпады; имя сокращается «…»."""
        row_h = 34.0
        gap = SP[2]
        p.setFont(self._font(FS_CAPTION + 0.5, True))
        names = [f"{r.slot + 1} · {r.name}" for r in self._rows]
        name_w = min(max((p.fontMetrics().horizontalAdvance(n) for n in names), default=0), body.width() * 0.3)
        tally_w = max((self._tag(r.tally).width() for r in self._rows if r.tally), default=0)
        track_w = max((self._tag(r.track).width() for r in self._rows), default=0)
        pads_n = max((min(2, len(r.pads)) for r in self._rows), default=0)
        pads_w = pads_n * PadBadge.W + max(0, pads_n - 1) * SP[0]
        p.setFont(self._font(FS_CAPTION))
        fm = p.fontMetrics()
        fps_texts = [self._fps_text(r) for r in self._rows]
        fps_w = max((fm.horizontalAdvance(t) for t in fps_texts), default=0)
        cal_w = fm.horizontalAdvance("нет калибровки") + SP[1] + 8
        fixed = name_w + (tally_w + gap if tally_w else 0) + track_w + gap + cal_w
        show_pads = pads_w and fixed + pads_w + gap <= body.width()
        show_fps = fixed + (pads_w + gap if show_pads else 0) + fps_w + gap <= body.width()
        for i, r in enumerate(self._rows):
            y = body.top() + i * row_h
            if y + row_h > body.bottom():
                break
            cy = y + row_h / 2
            x = body.left()
            p.setFont(self._font(FS_CAPTION + 0.5, True))
            p.setPen(qcolor(C.TEXT))
            p.drawText(QRectF(x, y, name_w, row_h), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(names[i], Qt.ElideRight, int(name_w)))
            x += name_w + gap
            if tally_w:
                if r.tally:
                    t = self._tag(r.tally)
                    self._draw_widget(p, t, x, cy - t.height() / 2)
                x += tally_w + gap
            t = self._tag(r.track)
            self._draw_widget(p, t, x, cy - t.height() / 2)
            x += track_w + gap
            if show_pads:
                px = x
                for n in r.pads[:2]:
                    pct, chg = r.battery.get(n, (None, False))
                    self._badge.set(n, pct, chg)
                    px += self._draw_widget(p, self._badge, px, cy - PadBadge.H / 2) + SP[0]
                x += pads_w + gap
            p.setFont(self._font(FS_CAPTION))
            p.setPen(qcolor(C.TEXT_DIM))
            if show_fps:
                p.drawText(QRectF(x, y, fps_w, row_h), Qt.AlignLeft | Qt.AlignVCenter, fps_texts[i])
            cal_x = body.right() - cal_w
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.LIME if r.calibrated else C.LOST))
            p.drawEllipse(QPointF(cal_x + 4, cy), 3.5, 3.5)
            p.setPen(qcolor(C.TEXT_DIM))
            p.drawText(QRectF(cal_x + 8 + SP[1], y, cal_w - 8 - SP[1], row_h), Qt.AlignLeft | Qt.AlignVCenter,
                       "калибровка" if r.calibrated else "нет калибровки")
            p.setPen(QPen(qcolor(C.LINE), 1))
            p.drawLine(QPointF(body.left(), y + row_h - 0.5), QPointF(body.right(), y + row_h - 0.5))

    @staticmethod
    def _fps_text(r: ServiceRow) -> str:
        t = f"{r.video_fps:.0f} к/с" if r.video_fps is not None else "нет видео"
        return t + (f" · анализ {r.analysis_fps:.0f}" if r.analysis_fps is not None else "")


class WelcomeView(QWidget):
    """Камер нет совсем: знак и два пути — добавить камеру или открыть симулятор."""

    addRequested = Signal()
    simRequested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"WelcomeView {{ background: {C.SURFACE}; }}")
        title = QLabel("Добавьте первую камеру")
        title.setStyleSheet(f"color: {C.TEXT}; font-size: 15px; font-weight: 600;")
        hint = QLabel("Камера и компьютер должны быть в одной сети")
        hint.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        self.add_btn = QPushButton("+ Добавить камеру")
        self.add_btn.setStyleSheet(f"QPushButton {{ background: {C.TEXT}; color: {C.BG}; border-color: {C.TEXT};"
                                   " font-weight: 600; }")
        self.sim_btn = QPushButton("Открыть симулятор")
        self.add_btn.clicked.connect(self.addRequested)
        self.sim_btn.clicked.connect(self.simRequested)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.add_btn)
        btns.addWidget(self.sim_btn)
        btns.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        lay.addWidget(BrandMark(64), 0, Qt.AlignHCenter)
        lay.addSpacing(SP[2])
        lay.addWidget(title, 0, Qt.AlignHCenter)
        lay.addLayout(btns)
        lay.addWidget(hint, 0, Qt.AlignHCenter)
        lay.addStretch(1)
