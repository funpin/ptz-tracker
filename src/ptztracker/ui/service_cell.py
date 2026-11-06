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
from ptztracker.multicam.timeline import Segment
from ptztracker.ui.theme import FONT_FAMILY, FS_CAPTION, R_PANEL, R_TAG, SP, C, qcolor
from ptztracker.ui.widgets import BrandMark, PadBadge, StateTag

Kind = Literal["position", "graphs", "summary"]
KINDS: tuple[Kind, ...] = ("summary", "position", "graphs")  # порядок, в котором занимают пустые клетки
GRAPH_SECONDS = 30.0
TITLES = {"position": "Положение камер", "graphs": "Графики PTZ", "summary": "Сводка"}
NEAR_LIMIT_DEG = 15.0  # ближе к пределу поворота — оранжевая подпись
TILT_RANGE = (-30.0, 90.0)  # шкала наклона на карточке, °
ZOOM_MAX_X = HFOV_WIDE_DEG / HFOV_TELE_DEG
GAUGES_MIN_W = 260  # уже — карточка положения без шкал наклона и зума


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
    # «Сводка» v2: дорожка за 10 мин, черта эфира, часы, итоги сеанса (multicam/timeline.py)
    timeline: tuple[Segment, ...] = ()
    air: tuple[Segment, ...] = ()
    clock: tuple[str, float | None] = ("off", None)
    air_total: float = 0.0
    losses: int = 0
    span_s: float = 0.0


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
    """Карточки в ряд, если так каждая не уже 0,75 высоты (широкая низкая клетка); иначе 3–4 — сеткой 2×2."""
    if n <= 0:
        return []
    in_row = n <= 2 or (rect.width() - SP[2] * (n - 1)) / n >= 0.75 * rect.height()
    cols = n if in_row else 2
    rows = 1 if in_row else 2
    gap = SP[2]
    w = (rect.width() - gap * (cols - 1)) / cols
    h = (rect.height() - gap * (rows - 1)) / rows
    return [QRectF(rect.left() + (i % cols) * (w + gap), rect.top() + (i // cols) * (h + gap), w, h)
            for i in range(n)]


def tile_colors(tally: str | None, selected: bool = False) -> tuple[str, str]:
    """Плитка номера камеры — везде одинаково: цветом эфира; без эфира у выбранной — белая (брендбук)."""
    return {"pgm": (C.PGM, C.TEXT), "pvw": (C.PVW, C.BG)}.get(
        tally or "", (C.TEXT, C.BG) if selected else (C.RAISED_HI, C.TEXT))


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
        self.footer = ""  # «Сводка»: строка общего под таблицей (ATEM, журнал, агент)
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
        bg, fg = tile_colors(r.tally, sel)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(bg))
        p.drawRoundedRect(num, R_TAG, R_TAG)
        p.setFont(self._font(11, True))
        p.setPen(qcolor(fg))
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
            # порядок — как шкалы справа (зум, затем наклон у края): цифра наклона — под своей шкалой
            vals = (f"P {g.pan:+.1f}°", f"{g.zoom_x:.1f}× · {2 * g.half_fov:.0f}°", f"T {g.tilt:+.1f}°")
            third = (card.width() - 2 * SP[1]) / 3
            for k, v in enumerate(vals):
                align = (Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignRight)[k] | Qt.AlignVCenter
                p.drawText(QRectF(card.left() + SP[1] + k * third, vy, third, vals_h), align, v)
        else:
            p.drawText(QRectF(card.left(), vy, card.width(), vals_h), Qt.AlignCenter, "нет данных")
        # середина: компас слева, шкалы наклона и зума справа
        mid = QRectF(card.left() + SP[1], hy + 18 + SP[0], card.width() - 2 * SP[1], vy - hy - 18 - 2 * SP[0])
        # шкалы наклона и зума — только в крупной карточке; в малой они нечитаемы, цифры есть внизу
        gauges_w = 52.0 if card.width() >= GAUGES_MIN_W else 0.0
        side = min(mid.width() - gauges_w, mid.height())
        if side < 30:
            p.setOpacity(1.0)
            return
        dial = QRectF(mid.left() + (mid.width() - gauges_w - side) / 2, mid.top() + (mid.height() - side) / 2,
                      side, side)
        self._paint_dial(p, r, g, dial, sel)
        if not gauges_w:
            p.setOpacity(1.0)
            return
        gx = mid.right() - gauges_w + SP[1]
        tilt_k = (g.tilt - TILT_RANGE[0]) / (TILT_RANGE[1] - TILT_RANGE[0])
        zoom_k = math.log(g.zoom_x) / math.log(ZOOM_MAX_X)
        for k, (label, frac) in enumerate((("зум", zoom_k), ("наклон", tilt_k))):
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
        """Строка на камеру, столбцы «поворот / наклон / зум»: короткий график за GRAPH_SECONDS и значение в углу.
        Лайм — камера ведёт цель, светло-серый — нет (тот же смысл, что у положения)."""
        n = len(self._rows)
        if not n:
            return
        head_h, name_w, gap = 14.0, min(96.0, body.width() * 0.18), SP[1] - 2
        col_w = (body.width() - name_w - gap * 3) / 3
        p.setFont(self._font(9.5, True))
        p.setPen(qcolor(C.TEXT_FAINT))
        for k, t in enumerate(("поворот", "наклон", "зум")):
            p.drawText(QRectF(body.left() + name_w + gap + k * (col_w + gap), body.top(), col_w, head_h),
                       Qt.AlignLeft | Qt.AlignVCenter, t.upper())
        row_h = min(44.0, (body.height() - head_h - SP[0] - gap * (n - 1)) / n)
        if row_h < 18:
            return
        now = time.monotonic()
        for i, r in enumerate(self._rows):
            y = body.top() + head_h + SP[0] + i * (row_h + gap)
            tile = QRectF(body.left(), y + row_h / 2 - 9, 18, 18)
            bg, fg = tile_colors(r.tally)
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(bg))
            p.drawRoundedRect(tile, R_TAG, R_TAG)
            p.setFont(self._font(11, True))
            p.setPen(qcolor(fg))
            p.drawText(tile, Qt.AlignCenter, str(r.slot + 1))
            p.setFont(self._font(FS_CAPTION - 1, True))
            p.setPen(qcolor(C.TEXT))
            nw = name_w - 18 - SP[1]
            p.drawText(QRectF(tile.right() + SP[1] - 2, y, nw, row_h), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(r.name, Qt.ElideRight, int(nw)))
            color = C.LIME if r.track == "on" else C.TEXT_DIM
            xs = [1.0 - (now - h.t) / GRAPH_SECONDS for h in r.history]
            series = ([h.pan_deg for h in r.history], [h.tilt_deg for h in r.history],
                      [h.zoom_x if h.zoom_x is not None else math.nan for h in r.history])
            for k, ys in enumerate(series):
                box = QRectF(body.left() + name_w + gap + k * (col_w + gap), y, col_w, row_h)
                p.setPen(QPen(qcolor(C.LINE), 1))
                p.setBrush(qcolor(C.BG))
                p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
                good = [v for v in ys if not math.isnan(v)]
                if not good:
                    continue
                lo, hi = min(good), max(good)
                span = (hi - lo) or 1.0
                pts = [QPointF(box.left() + 3 + max(0.0, min(1.0, x)) * (box.width() - 6),
                               box.bottom() - 4 - (v - lo) / span * (box.height() - 8))
                       for x, v in zip(xs, ys, strict=True) if not math.isnan(v)]
                if len(pts) > 1:
                    p.setPen(QPen(qcolor(color), 1.5))
                    p.drawPolyline(pts)
                text = f"{good[-1]:.1f}×" if k == 2 else f"{good[-1]:+.1f}°"
                p.setFont(self._font(FS_CAPTION - 1.5))
                p.setPen(qcolor(C.TEXT))
                p.drawText(box.adjusted(0, 1, -5, 0), Qt.AlignRight | Qt.AlignTop, text)

    # «Сводка» v2 (пользователь 2026-11-05): строка на камеру — дорожка за 10 мин, под ней черта эфира,
    # справа крупные часы. Ярлыки, к/с, геймпады и калибровка уже есть в шапках клеток — здесь не повторяем.
    CLOCK_TEXT = {"air": "эфир", "on": "ведёт", "lost": "ищет", "off": "вручную", "standby": "ожидание",
                  "no_analysis": "без анализа"}
    TRACK_COLOR = {"on": C.LIME, "lost": C.LOST}  # остальное — серым: «вручную»

    @staticmethod
    def _mmss(sec: float | None) -> str:
        if sec is None:
            return "—"
        sec = int(sec)
        return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"

    def summary_lines(self) -> list[str]:
        out = []
        for r in self._rows:
            kind, sec = r.clock
            out.append(f"{r.slot + 1} · {r.name} | {self.CLOCK_TEXT.get(kind, kind)} {self._mmss(sec)}")
        return out

    def day_text(self) -> str:
        on_air = [f"{r.name} {round(r.air_total / 60)} мин" for r in self._rows if r.air_total >= 30]
        n = sum(r.losses for r in self._rows)
        times = "раза" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "раз"
        parts = (["в эфире " + ", ".join(on_air)] if on_air else []) + [f"цель терялась {n} {times}"]
        return "За сеанс: " + " · ".join(parts)

    def _paint_summary(self, p: QPainter, body: QRectF) -> None:
        foot_h = 16.0
        n = len(self._rows)
        if not n:
            return
        gap = SP[1] - 2
        row_h = min(52.0, (body.height() - foot_h - SP[1] - gap * (n - 1)) / n)
        clock_w = 74.0
        for i, r in enumerate(self._rows):
            y = body.top() + i * (row_h + gap)
            if row_h < 26:
                break
            box = QRectF(body.left(), y, body.width(), row_h)
            p.setPen(QPen(qcolor(C.LINE), 1))
            p.setBrush(qcolor(C.BG))
            p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), R_PANEL - 2, R_PANEL - 2)
            cy = box.center().y()
            # номер — плитка цветом эфира, как в шапке клетки
            tile = QRectF(box.left() + SP[1] + 2, cy - 9, 18, 18)
            bg, fg = tile_colors(r.tally)
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(bg))
            p.drawRoundedRect(tile, R_TAG, R_TAG)
            p.setFont(self._font(11, True))
            p.setPen(qcolor(fg))
            p.drawText(tile, Qt.AlignCenter, str(r.slot + 1))
            name_w = min(110.0, box.width() * 0.2)
            p.setFont(self._font(FS_CAPTION, True))
            p.setPen(qcolor(C.TEXT))
            p.drawText(QRectF(tile.right() + SP[1], box.top(), name_w, row_h), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(r.name, Qt.ElideRight, int(name_w)))
            # дорожка и черта эфира
            bar = QRectF(tile.right() + SP[1] + name_w + SP[1], cy - 6, 0, 9)
            bar.setRight(box.right() - clock_w - SP[2])
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.RAISED))
            p.drawRoundedRect(bar, 3, 3)
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(bar, 3, 3)
            p.setClipPath(clip)
            for sg in r.timeline:
                p.fillRect(QRectF(bar.left() + sg.f0 * bar.width(), bar.top(), (sg.f1 - sg.f0) * bar.width(),
                                  bar.height()), qcolor(self.TRACK_COLOR.get(sg.state, C.GREY)))
            p.restore()
            for sg in r.air:
                p.fillRect(QRectF(bar.left() + sg.f0 * bar.width(), bar.bottom() + 2, (sg.f1 - sg.f0) * bar.width(),
                                  3), qcolor(C.PGM))
            # часы
            kind, sec = r.clock
            col = {"air": C.PGM, "on": C.LIME, "lost": C.LOST}.get(kind, C.TEXT_FAINT)
            cr = QRectF(box.right() - clock_w - SP[1], box.top() + 2, clock_w, row_h - 4)
            p.setFont(self._font(9.5, True))
            p.setPen(qcolor(col))
            big = row_h >= 40
            p.drawText(QRectF(cr.left(), cr.top() + (2 if big else 0), cr.width(), 12),
                       Qt.AlignRight | Qt.AlignVCenter, self.CLOCK_TEXT.get(kind, kind).upper())
            if big or sec is not None:
                p.setFont(self._font(18 if big else 13, True))
                p.setPen(qcolor(C.TEXT if sec is not None else C.TEXT_FAINT))
                p.drawText(QRectF(cr.left() - 20, cr.top() + (14 if big else 10), cr.width() + 20, cr.height() - 14),
                           Qt.AlignRight | Qt.AlignVCenter, self._mmss(sec))
        # низ: легенда слева, итоги сеанса справа
        fy = body.bottom() - foot_h
        p.setFont(self._font(FS_CAPTION - 1.5))
        x = body.left()
        for text, col in (("ведёт", C.LIME), ("ищет", C.LOST), ("вручную", C.GREY), ("эфир", C.PGM)):
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(col))
            p.drawRoundedRect(QRectF(x, fy + foot_h / 2 - 3.5, 7, 7), 2, 2)
            p.setPen(qcolor(C.TEXT_FAINT))
            w = p.fontMetrics().horizontalAdvance(text)
            p.drawText(QRectF(x + 10, fy, w + 2, foot_h), Qt.AlignLeft | Qt.AlignVCenter, text)
            x += 10 + w + SP[2]
        right = self.day_text()
        p.drawText(QRectF(x, fy, body.right() - x, foot_h), Qt.AlignRight | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(right, Qt.ElideLeft, int(body.right() - x)))


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
