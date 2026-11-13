from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from ptztracker.control import stage_area as sa
from ptztracker.control.zones import Zone, zone_hint
from ptztracker.ui.state import no_video_text
from ptztracker.ui.theme import (
    FONT_FAMILY,
    FS_CAPTION,
    R_TAG,
    REC_BLINK_S,
    SP,
    STAGE_DIM,
    STAGE_DIM_EDIT,
    STAGE_HANDLE,
    STAGE_LINE_ALPHA,
    STAGE_LINE_ALPHA_EDIT,
    STAGE_LINE_W,
    STAGE_LINE_W_EDIT,
    C,
    qcolor,
)
from ptztracker.ui.widgets import paint_brackets
from ptztracker.video.frames import FrameSource
from ptztracker.videorec.controller import format_clock
from ptztracker.videorec.recorder import RecState
from ptztracker.vision.types import (
    KP_L_ANKLE,
    KP_L_HIP,
    KP_L_KNEE,
    KP_L_SHOULDER,
    KP_NOSE,
    KP_R_ANKLE,
    KP_R_HIP,
    KP_R_KNEE,
    KP_R_SHOULDER,
)

MOUSE_DEAD = 0.03  # режим мыши: отклонение от центра видео (доля ширины), меньше — дрожь руки
MOUSE_FULL = 0.35  # дальше — полная скорость, как упор стика


def mouse_velocity(dx: float, dy: float) -> tuple[float, float]:
    """Режим мыши: смещение указателя от центра видео (доли ширины, вниз — плюс) → скорость (-1…1, вверх — плюс)."""
    def axis(v: float) -> float:
        a = abs(v)
        if a < MOUSE_DEAD:
            return 0.0
        return round(min(1.0, (a - MOUSE_DEAD) / (MOUSE_FULL - MOUSE_DEAD)) * (1 if v > 0 else -1), 3) + 0.0
    return axis(dx), axis(-dy)


TARGET_COLOR = qcolor(C.ON)
LOST_COLOR = qcolor(C.LOST)
OTHER_COLOR = qcolor(C.TEXT, 170)
SKELETON = [
    (KP_L_SHOULDER, KP_R_SHOULDER),
    (KP_L_SHOULDER, KP_L_HIP),
    (KP_R_SHOULDER, KP_R_HIP),
    (KP_L_HIP, KP_R_HIP),
    (KP_L_HIP, KP_L_KNEE),
    (KP_L_KNEE, KP_L_ANKLE),
    (KP_R_HIP, KP_R_KNEE),
    (KP_R_KNEE, KP_R_ANKLE),
]
KP_MIN_CONF = 0.3


REC_MARGIN, REC_H, REC_PAD_X, REC_DOT = 10, 24, 8, 8


def dataclass_on(area: sa.StageArea) -> sa.StageArea:
    """Та же область, но включённая: при настройке людей считаем и в выключенной."""
    return area if area.on else sa.StageArea(area.pan0, area.pan1, area.tilt0, area.tilt1, True)


class PreviewWidget(QWidget):
    """Превью последнего кадра камеры с рамками людей; клик по человеку выбирает цель."""

    targetClicked = Signal(int)
    imageSizeChanged = Signal()  # пришёл первый кадр или сменилось разрешение — плашки поверх видео к картинке
    # своя зона (control.zones): оператор тянет её мышью — точка лица (доли кадра), ширина и высота зоны
    zoneChanged = Signal(float, float, float, float)
    zoneCommitted = Signal()  # отпустил кнопку — можно сохранять
    zoneReset = Signal()  # двойной клик по зоне
    zoneHoverChanged = Signal(bool)  # мышь над зоной — подсказка внизу видео показывает, как её тянуть
    # режим «камера как курсор» (макет mouse-mode.html): окно двигает камеру по этим сигналам
    mouseVector = Signal(float, float)  # скорость поворота и наклона −1…1
    wheelStep = Signal(int)  # +1 — колесо от себя
    leftClicked = Signal()
    rightClicked = Signal()
    rec_clicked = Signal()  # кнопка или плашка записи в углу видео
    doubleClicked = Signal()  # двойной клик по видео вне зоны — войти в режим мыши
    stageEdited = Signal()  # при настройке области сцены сторону перетащили (stage_draft уже новая)

    click_guard: Callable[[], bool] | None = None  # False — клик не выбирает человека
    # (ширина, высота) сохранённой зоны этой камеры относительно обычной (по умолчанию 1 и 1)
    zone_size_of: Callable[[], tuple[float, float]] | None = None
    DRAG_PX = 6  # меньше — это клик (выбор человека), больше — перенос зоны
    HANDLE_PX = 12  # скобка-уголок мягкой зоны ловится в этом радиусе
    STAGE_GRAB = 0.025  # сторона области сцены ловится не дальше этой доли ширины кадра

    def __init__(self, parent=None):
        super().__init__(parent)
        self.camera_name = ""  # для текста «Нет видео с камеры …»
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._source: FrameSource | None = None
        self._analyzer = None
        self._follower = None
        self._image: QImage | None = None
        self._seq = -1
        self._an_seq = None
        self.show_skeleton = False
        self._press = None  # (точка нажатия, что под мышью, точка лица, мягкая зона, масштаб) — пока кнопка нажата
        self._drag: str | None = None  # "move" | "nw" | "se"
        self._hover: str | None = None
        self.mouse_mode = False
        self._mouse_pt: QPointF | None = None
        self._rec: RecState | None = None  # None — камеры нет, кнопки записи нет
        self._rec_mouse: QPointF | None = None
        self._rec_key = None  # что нарисовано: перерисовать, когда сменилось
        # область сцены: (момент кадра) → (StageArea | None, CameraView | None); ставит окно (сессия камеры)
        self.stage_source: Callable[[float], tuple] | None = None
        self.stage_draft: sa.StageArea | None = None  # идёт настройка: эту область рисуем и тянем мышью
        self._stage_side: str | None = None  # сторона, которую тянут
        self._frame_t = 0.0  # момент показанного кадра
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    # --- источники ------------------------------------------------------

    def set_source(self, source: FrameSource | None) -> None:
        self._source = source
        self._image = None
        self._seq = -1
        self.update()

    def set_analyzer(self, analyzer) -> None:
        self._analyzer = analyzer
        self._an_seq = None
        self.update()

    def set_follow(self, follower) -> None:
        self._follower = follower
        self.update()

    def follow_zones(self):
        """(точка x, точка y, мёртвая зона, мягкая зона, сдвиг x) в долях кадра — или None, если слежение выключено.
        Сдвига точки больше нет (всегда 0): точка и рамка стоят, где их поставил оператор, а идущего регулятор сам
        держит чуть впереди точки (framing.LEAD)."""
        f = self._follower
        if f is None or not f.enabled or f.output is None:
            return None
        c = f.controller
        e = c.effort
        ax, ay = c.composition.anchor
        return (ax, ay, (e.dead_x, e.dead_y), (e.soft_x, e.soft_y), 0.0)

    def has_frame(self) -> bool:
        return self._image is not None

    def _result(self):
        return self._analyzer.latest() if self._analyzer is not None else None

    def _tick(self) -> None:
        changed = False
        fr = self._source.latest() if self._source else None
        if fr is None:
            if self._image is not None:
                self._image = None
                changed = True
        elif fr.seq != self._seq:
            h, w, _ = fr.image.shape
            # .copy(): QImage не владеет буфером numpy
            self._image = QImage(fr.image.data, w, h, fr.image.strides[0], QImage.Format_BGR888).copy()
            self._seq = fr.seq
            self._frame_t = fr.t_capture
            changed = True
        r = self._result()
        key = (None if r is None else (r.seq, r.target_id, r.target_state), self.follow_zones())
        if key != self._an_seq:
            self._an_seq = key
            changed = True
        size = None if self._image is None else (self._image.width(), self._image.height())
        if size != getattr(self, "_shown_size", None):
            self._shown_size = size
            self.imageSizeChanged.emit()
        if changed or self._rec_view_changed():
            self.update()

    # --- запись видео ---------------------------------------------------

    def set_rec(self, state: RecState | None) -> None:
        self._rec = state
        if self._rec_view_changed():
            self.update()

    def _rec_view_changed(self) -> bool:
        key = (self.rec_text() if self._rec is not None else None, self._rec_blink())
        changed, self._rec_key = key != self._rec_key, key
        return changed

    @staticmethod
    def _rec_blink() -> bool:
        return int(time.monotonic() / REC_BLINK_S) % 2 == 0

    def _rec_visible(self) -> bool:
        if self._rec is None:
            return False
        return self._rec.active or (self._rec_mouse is not None and not self.mouse_mode)

    def _rec_font(self) -> QFont:
        f = QFont(FONT_FAMILY)
        f.setPixelSize(FS_CAPTION)
        f.setBold(True)
        return f

    def _rec_plate(self, text: str) -> QRectF:
        w = QFontMetricsF(self._rec_font()).horizontalAdvance(text) + REC_PAD_X * 2 + REC_DOT + SP[0]
        area = self._image_rect() or QRectF(self.rect())
        return QRectF(area.right() - w - REC_MARGIN, area.top() + REC_MARGIN, w, REC_H)

    def rec_rect(self) -> QRectF | None:
        return self._rec_plate(self._rec_steady_text()) if self._rec_visible() else None

    def _rec_steady_text(self) -> str:
        """Текст, по которому считается плашка: под мышью она не должна сжиматься, иначе клик уйдёт мимо."""
        s = self._rec
        if s is None or not s.active:
            return "Запись"
        return f"REC {format_clock(s.elapsed_s)}" if s.signal else "REC нет сигнала"

    def rec_text(self) -> str:
        s = self._rec
        return "■ Стоп" if s is not None and self._rec_hovered(s) else self._rec_steady_text()

    def rec_dot_color(self) -> str:
        return C.LOST if self._rec is not None and self._rec.degraded else C.REC

    def _rec_hovered(self, s: RecState) -> bool:
        if self._rec_mouse is None or self.mouse_mode or not s.active:
            return False
        return self._rec_plate(self._rec_steady_text()).contains(self._rec_mouse)

    def _rec_hit(self, pos: QPointF) -> bool:
        r = None if self.mouse_mode else self.rec_rect()
        return r is not None and r.contains(pos)

    def _paint_rec(self, p: QPainter) -> None:
        r = self.rec_rect()
        if r is None:
            return
        text = self.rec_text()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.BG, 214))
        p.drawRoundedRect(r, R_TAG + 1, R_TAG + 1)
        stopping = text == "■ Стоп"
        if not stopping and (self._rec_blink() or not self._rec.active):
            p.setBrush(qcolor(self.rec_dot_color()))
            p.drawEllipse(QPointF(r.left() + REC_PAD_X + REC_DOT / 2, r.center().y()), REC_DOT / 2, REC_DOT / 2)
        p.setFont(self._rec_font())
        p.setPen(qcolor(C.REC if stopping else C.TEXT))
        p.drawText(r.adjusted(REC_PAD_X + REC_DOT + SP[0], 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)

    # --- геометрия ------------------------------------------------------

    def _image_rect(self) -> QRectF | None:
        if self._image is None:
            return None
        iw, ih = self._image.width(), self._image.height()
        scale = min(self.width() / iw, self.height() / ih)
        w, h = iw * scale, ih * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def widget_to_frame(self, pt: QPointF) -> tuple[float, float] | None:
        r = self._image_rect()
        if r is None or not r.contains(pt):
            return None
        k = self._image.width() / r.width()
        return (pt.x() - r.x()) * k, (pt.y() - r.y()) * k

    def frame_to_widget(self, x: float, y: float) -> QPointF:
        r = self._image_rect()
        if r is None:
            return QPointF(0, 0)
        k = r.width() / self._image.width()
        return QPointF(r.x() + x * k, r.y() + y * k)

    def _box_rect(self, box, frame_w: int) -> QRectF:
        r = self._image_rect()
        k = r.width() / frame_w
        return QRectF(r.x() + box[0] * k, r.y() + box[1] * k, (box[2] - box[0]) * k, (box[3] - box[1]) * k)

    # --- мышь -----------------------------------------------------------

    def _seen_track_at(self, pt: QPointF):
        r = self._result()
        if r is None or self._image is None:
            return None
        hits = [t for t in r.tracks if t.last_seen == r.t_capture and self._box_rect(t.box, r.frame_size[0]).contains(pt)]
        return min(hits, key=lambda t: (t.box[2] - t.box[0]) * (t.box[3] - t.box[1])) if hits else None

    def _zone_rects(self):
        """(мягкая, мёртвая, точка лица) в координатах виджета — или None, если зоны не видны."""
        z = self.follow_zones()
        r = self._image_rect()
        if z is None or r is None:
            return None
        cx, cy, dead, soft, lead = z

        def rect(hw, hh):
            return QRectF(r.x() + (cx - hw) * r.width(), r.y() + (cy - hh) * r.height(),
                          2 * hw * r.width(), 2 * hh * r.height())

        return rect(*soft), rect(*dead), QPointF(r.x() + (cx + lead) * r.width(), r.y() + cy * r.height())

    def _zone_hit(self, pt: QPointF) -> str | None:
        z = self._zone_rects()
        if z is None:
            return None
        soft = z[0]
        for name, c in (("nw", soft.topLeft()), ("se", soft.bottomRight())):
            if abs(pt.x() - c.x()) <= self.HANDLE_PX and abs(pt.y() - c.y()) <= self.HANDLE_PX:
                return name
        return "move" if soft.contains(pt) else None

    def set_mouse_mode(self, on: bool) -> None:
        """Камера как курсор: курсор прячется, в центре — крестик, по краю — лаймовая рамка."""
        self.mouse_mode = on
        self._mouse_pt = None
        self._press, self._drag = None, None
        self.setCursor(Qt.BlankCursor if on else Qt.ArrowCursor)
        if not on:
            self.mouseVector.emit(0.0, 0.0)
        self.update()

    def _mouse_mode_move(self, pos: QPointF) -> None:
        self._mouse_pt = pos
        w = max(1.0, float(self.width()))
        self.mouseVector.emit(*mouse_velocity((pos.x() - self.width() / 2) / w, (pos.y() - self.height() / 2) / w))
        self.update()

    def wheelEvent(self, e):  # noqa: N802
        if self.mouse_mode and e.angleDelta().y():
            self.wheelStep.emit(1 if e.angleDelta().y() > 0 else -1)
            e.accept()
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e):  # noqa: N802
        if self.stage_draft is not None:  # настройка области сцены: мышь только тянет её стороны
            if e.button() == Qt.LeftButton:
                self._stage_side = self._stage_side_at(e.position())
            return
        if self.mouse_mode:
            if e.button() == Qt.LeftButton:
                self.leftClicked.emit()
            elif e.button() == Qt.RightButton:
                self.rightClicked.emit()
            return
        if e.button() != Qt.LeftButton:
            return
        if self._rec_hit(e.position()):
            self.rec_clicked.emit()
            return
        if self.click_guard is not None and not self.click_guard():
            return
        pos = e.position()
        hit = self._zone_hit(pos)
        if hit is None:
            self._select_at(pos)  # вне зоны — выбор человека сразу, как раньше
            return
        f = self._follower.controller
        size = self.zone_size_of() if self.zone_size_of is not None else (1.0, 1.0)
        self._press = (pos, hit, f.composition.anchor, self._zone_rects()[0], size)
        if hit != "move":
            self._drag = hit  # за уголок тянут сразу

    def mouseMoveEvent(self, e):  # noqa: N802
        pos = e.position()
        self._rec_mouse = pos
        if self._rec_view_changed():
            self.update()
        if self.stage_draft is not None:
            if self._stage_side is not None:
                self._stage_drag_to(pos)
            else:
                side = self._stage_side_at(pos)
                self.setCursor(Qt.ArrowCursor if side is None else Qt.SizeHorCursor if side.startswith("pan")
                               else Qt.SizeVerCursor)
            return
        if self.mouse_mode:
            self._mouse_mode_move(pos)
            return
        if self._press is not None:
            p0, hit, (ax, ay), soft0, (w0, h0) = self._press
            if self._drag is None and (pos - p0).manhattanLength() > self.DRAG_PX:
                self._drag = hit
            if self._drag is not None:
                r = self._image_rect()
                if self._drag == "move":
                    x, y, w, h = ax + (pos.x() - p0.x()) / r.width(), ay + (pos.y() - p0.y()) / r.height(), w0, h0
                else:  # уголок: зона растёт от своего центра, ширина и высота — каждая сама по себе
                    c = soft0.center()
                    x, y = ax, ay
                    w = w0 * abs(pos.x() - c.x()) / (soft0.width() / 2)
                    h = h0 * abs(pos.y() - c.y()) / (soft0.height() / 2)
                z = Zone.clamped(x, y, w, h)
                self.zoneChanged.emit(z.x, z.y, z.w, z.h)
                self.update()
                return
        hover = self._zone_hit(pos)
        if hover != self._hover:
            was = self._hover is not None
            self._hover = hover
            self.update()
            if was != (hover is not None):
                self.zoneHoverChanged.emit(hover is not None)
        if hover in ("nw", "se"):
            self.setCursor(Qt.SizeFDiagCursor)
        elif self._seen_track_at(pos):
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.SizeAllCursor if hover else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self.stage_draft is not None:
            self._stage_side = None
            return
        if e.button() != Qt.LeftButton or self._press is None:
            return
        p0 = self._press[0]
        dragged, self._press, self._drag = self._drag is not None, None, None
        if dragged:
            self.zoneCommitted.emit()
        else:
            self._select_at(p0)  # внутри зоны без переноса — обычный клик по человеку
        self.update()

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if self.stage_draft is not None:  # вернуть в кадр стороны, ушедшие за него
            _, view = self._stage()
            if view is not None and e.button() == Qt.LeftButton:
                self.stage_draft = sa.fit_hidden_sides(self.stage_draft, view)
                self.stageEdited.emit()
                self.update()
            e.accept()
            return
        if self.mouse_mode or e.button() != Qt.LeftButton or self._rec_hit(e.position()):
            return
        if self.click_guard is not None and not self.click_guard():
            return
        if self._zone_hit(e.position()) is not None:
            self.zoneReset.emit()
        elif self.widget_to_frame(e.position()) is not None:
            self.doubleClicked.emit()  # по видео вне зоны — режим мыши
        e.accept()

    def zone_hovered(self) -> bool:
        return self._hover is not None or self._drag is not None

    def zone_dragging(self) -> bool:
        return self._drag is not None

    def zone_text(self) -> str:
        """Плашка зоны: её размер в процентах кадра и что она значит для этой камеры и потока («» — зоны нет)."""
        z = self.follow_zones()
        if z is None:
            return ""
        soft = z[3]
        return zone_hint(soft[0], soft[1], float(getattr(self._follower.controller, "t_late", 0.0)))

    def leaveEvent(self, e):  # noqa: N802
        self._rec_mouse = None
        if self._rec_view_changed():
            self.update()
        if self.mouse_mode:  # указатель ушёл с видео — камера стоит
            self._mouse_pt = None
            self.mouseVector.emit(0.0, 0.0)
            self.update()
        if self._hover is not None:
            self._hover = None
            self.update()
            self.zoneHoverChanged.emit(False)
        super().leaveEvent(e)

    def _select_at(self, pos: QPointF) -> None:
        if self._analyzer is None:
            return
        r = self._result()
        fp = self.widget_to_frame(pos)
        if fp is None or r is None:
            return
        # кадр анализа может отличаться размером от показанного — пересчитать
        k = r.frame_size[0] / self._image.width()
        tid = self._analyzer.select_at(fp[0] * k, fp[1] * k)
        if tid is not None:
            self.targetClicked.emit(tid)
            self.update()

    # --- область сцены --------------------------------------------------

    def _stage(self) -> tuple[sa.StageArea | None, sa.CameraView | None]:
        """Область, которую сейчас показываем (при настройке — черновик), и вид камеры в показанном кадре."""
        if self.stage_source is None or self._image is None:
            return None, None
        try:
            area, view = self.stage_source(self._frame_t)
        except Exception:
            return None, None
        if self.stage_draft is not None:
            return self.stage_draft, view
        return (area, view) if area is not None and area.on else (None, view)

    def stage_outline(self) -> list[QPointF]:
        """Контур области на виджете; пусто — области нет, она выключена или вид камеры неизвестен."""
        area, view = self._stage()
        r = self._image_rect()
        if area is None or view is None or r is None:
            return []
        return [QPointF(r.x() + x * r.width(), r.y() + y * r.height()) for x, y in area.outline(view)]

    def shown_tracks(self) -> list:
        """Люди, которым рисуем рамки: вне области сцены — только цель."""
        r = self._result()
        if r is None:
            return []
        area, view = self._stage()
        if area is None or view is None or self.stage_draft is not None:
            return list(r.tracks)
        return [t for t in r.tracks if t.on_stage or t.id == r.target_id]

    def stage_counts(self) -> tuple[int, int] | None:
        """(людей в области, людей в кадре) — для плашки при настройке; None — посчитать не по чему."""
        area, view = self._stage()
        r = self._result()
        if area is None or view is None or r is None:
            return None
        seen = [t for t in r.tracks if t.last_seen == r.t_capture]
        flags = sa.flags(dataclass_on(area), view, seen, r.frame_size) or []
        return sum(flags), len(seen)

    def begin_stage_edit(self, area: sa.StageArea) -> None:
        self.stage_draft, self._stage_side = area, None
        self.update()

    def end_stage_edit(self) -> None:
        self.stage_draft = self._stage_side = None
        self.unsetCursor()
        self.update()

    def _frame_frac(self, pos: QPointF) -> tuple[float, float] | None:
        r = self._image_rect()
        if r is None or r.width() <= 0:
            return None
        return (pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height()

    def _stage_side_at(self, pos: QPointF) -> str | None:
        area, view = self._stage()
        fp = self._frame_frac(pos)
        if area is None or view is None or fp is None:
            return None
        return sa.side_at(area, view, fp[0], fp[1], self.STAGE_GRAB)

    def _stage_drag_to(self, pos: QPointF) -> None:
        _, view = self._stage()
        fp = self._frame_frac(pos)
        if view is None or fp is None or self._stage_side is None:
            return
        x, y = min(1.0, max(0.0, fp[0])), min(1.0, max(0.0, fp[1]))
        self.stage_draft = sa.drag_side(self.stage_draft, self._stage_side, *sa.to_world(view, x, y))
        self.stageEdited.emit()
        self.update()

    def _paint_stage(self, p: QPainter) -> None:
        pts = self.stage_outline()
        r = self._image_rect()
        if not pts or r is None:
            return
        edit = self.stage_draft is not None
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setClipRect(r)
        poly = QPainterPath()
        poly.addPolygon(QPolygonF(pts))
        poly.closeSubpath()
        shade = QPainterPath()
        shade.addRect(r)
        p.fillPath(shade.subtracted(poly), qcolor(C.BG, round(255 * (STAGE_DIM_EDIT if edit else STAGE_DIM))))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(qcolor(C.TEXT, round(255 * (STAGE_LINE_ALPHA_EDIT if edit else STAGE_LINE_ALPHA))),
                      STAGE_LINE_W_EDIT if edit else STAGE_LINE_W))
        p.drawPath(poly)
        if edit:
            area, view = self._stage()
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.TEXT))
            long, thick = STAGE_HANDLE
            for side, (x, y) in sa.handles(area, view).items():
                c = QPointF(r.x() + x * r.width(), r.y() + y * r.height())
                w, h = (thick, long) if side.startswith("pan") else (long, thick)
                p.drawRoundedRect(QRectF(c.x() - w / 2, c.y() - h / 2, w, h), 4, 4)
        p.restore()

    # --- отрисовка ------------------------------------------------------

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(C.BG))
        if self._image is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(self._image_rect(), self._image)
            self._paint_overlay(p)
            self._paint_rec(p)
            return
        p.setPen(qcolor(C.TEXT_DIM))
        f = QFont(FONT_FAMILY)
        f.setPointSize(18)
        p.setFont(f)
        if self._source is not None:
            text = no_video_text(self.camera_name, self._source.stats().error)
        else:
            text = "Камера не выбрана"
        p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, text)
        self._paint_rec(p)

    def _paint_zones(self, p: QPainter) -> None:
        """Зона слежения — знаком программы (макет zone-look.html): мягкая зона — две встречные скобки,
        мёртвая — тонкий скруглённый контур, точка лица — лаймовая."""
        z = self._zone_rects()
        if z is None:
            return
        soft, dead, pt = z
        live = self._hover is not None or self._drag is not None
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if live:  # навели или тянут — зона чуть подсвечена: её можно взять
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.TEXT, 13))
            p.drawRoundedRect(soft, 8, 8)
        arm = max(10.0, min(60.0, min(soft.width(), soft.height()) * 0.22))
        paint_brackets(p, soft, qcolor(C.TEXT, 240 if live else 150), 3, arm=arm)
        p.setPen(QPen(qcolor(C.TEXT, 130 if live else 72), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(dead, 6, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.ON))
        p.drawEllipse(pt, 4.5, 4.5)
        if self._drag is not None:  # как тянуть — в подсказке внизу видео; над зоной — где будет лицо
            a = self._follower.controller.composition.anchor
            text = f"Лицо: {round(a[0] * 100)} % · {round(a[1] * 100)} %"
            f = QFont(FONT_FAMILY)
            f.setPointSize(10)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(text) + 16
            th = p.fontMetrics().height() + 8
            y = soft.top() - th / 2 if soft.top() - th / 2 >= 0 else soft.top() + 4
            plate = QRectF(soft.left() + arm + 10, y, tw, th)
            p.setBrush(qcolor(C.BG, 217))  # плашка поверх видео: графит 85 %, скругление 7 (BRANDBOOK §6)
            p.drawRoundedRect(plate, 7, 7)
            p.setPen(qcolor(C.TEXT))
            p.drawText(plate, Qt.AlignCenter, text)
        p.restore()

    def _paint_mouse_mode(self, p: QPainter) -> None:
        """Лаймовая рамка по краю, крестик в центре и стрелка к указателю: куда и как быстро едет камера."""
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(qcolor(C.LIME), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        c = QPointF(self.width() / 2, self.height() / 2)
        p.setPen(QPen(qcolor(C.TEXT, 220), 2))
        p.drawLine(c - QPointF(11, 0), c + QPointF(11, 0))
        p.drawLine(c - QPointF(0, 11), c + QPointF(0, 11))
        pt = self._mouse_pt
        if pt is not None and (pt - c).manhattanLength() > MOUSE_DEAD * self.width():
            p.setPen(QPen(qcolor(C.LIME), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(c, pt)
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.LIME))
            p.drawEllipse(pt, 5, 5)
        p.restore()

    def _paint_overlay(self, p: QPainter) -> None:
        self._paint_stage(p)
        if self.mouse_mode:
            self._paint_mouse_mode(p)
        self._paint_zones(p)
        r = self._result()
        if r is None:
            return
        p.setRenderHint(QPainter.Antialiasing)
        fw = r.frame_size[0]
        k = self._image_rect().width() / fw
        for t in self.shown_tracks():
            rect = self._box_rect(t.box, fw)
            is_target = t.id == r.target_id
            if is_target:
                lost = r.target_state == "lost"
                color = LOST_COLOR if lost else TARGET_COLOR
                paint_brackets(p, rect, color, 3, corners=("tr", "bl"))
                self._label(p, rect, "ЦЕЛЬ ПОТЕРЯНА" if lost else "ВЕДУ", color)
            else:
                pen = QPen(OTHER_COLOR, 1)
                pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect)
            if self.show_skeleton and t.last_seen == r.t_capture:
                self._skeleton(p, t.keypoints, k, TARGET_COLOR if is_target else OTHER_COLOR)

    def _label(self, p: QPainter, rect: QRectF, text: str, color: QColor) -> None:
        # подпись ВНУТРИ рамки: на крупном плане рамка почти во весь кадр
        f = QFont(FONT_FAMILY)
        f.setPointSize(11)
        f.setBold(True)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(text) + 12
        th = p.fontMetrics().height() + 4
        plate = QRectF(rect.x() + 3, rect.y() + 3, tw, th)
        p.setPen(Qt.NoPen)
        bg = QColor(color)
        bg.setAlpha(190)
        p.setBrush(bg)
        p.drawRoundedRect(plate, 4, 4)
        p.setPen(qcolor(C.TEXT))
        p.drawText(plate, Qt.AlignCenter, text)

    def _skeleton(self, p: QPainter, kp, k: float, color: QColor) -> None:
        r = self._image_rect()

        def pt(i):
            return QPointF(r.x() + kp[i, 0] * k, r.y() + kp[i, 1] * k)

        p.setPen(QPen(color, 2))
        for a, b in SKELETON:
            if kp[a, 2] >= KP_MIN_CONF and kp[b, 2] >= KP_MIN_CONF:
                p.drawLine(pt(a), pt(b))
        if kp[KP_NOSE, 2] >= KP_MIN_CONF:
            p.setBrush(color)
            p.drawEllipse(pt(KP_NOSE), 4, 4)
