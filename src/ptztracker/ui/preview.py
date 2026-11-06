from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ptztracker.control.zones import Zone
from ptztracker.ui.state import no_video_text
from ptztracker.ui.theme import FONT_FAMILY, C, qcolor
from ptztracker.ui.widgets import paint_brackets
from ptztracker.video.frames import FrameSource
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


class PreviewWidget(QWidget):
    """Превью последнего кадра камеры с рамками людей; клик по человеку выбирает цель."""

    targetClicked = Signal(int)
    # своя зона (control.zones): оператор тянет её мышью — точка лица (доли кадра) и масштаб зон
    zoneChanged = Signal(float, float, float)
    zoneCommitted = Signal()  # отпустил кнопку — можно сохранять
    zoneReset = Signal()  # двойной клик по зоне
    zoneHoverChanged = Signal(bool)  # мышь над зоной — подсказка внизу видео показывает, как её тянуть
    # режим «камера как курсор» (макет mouse-mode.html): окно двигает камеру по этим сигналам
    mouseVector = Signal(float, float)  # скорость поворота и наклона −1…1
    wheelStep = Signal(int)  # +1 — колесо от себя
    leftClicked = Signal()
    rightClicked = Signal()
    doubleClicked = Signal()  # двойной клик по видео вне зоны — войти в режим мыши

    click_guard: Callable[[], bool] | None = None  # False — клик не выбирает человека
    zone_scale_of: Callable[[], float] | None = None  # масштаб сохранённой зоны этой камеры (по умолчанию 1)
    DRAG_PX = 6  # меньше — это клик (выбор человека), больше — перенос зоны
    HANDLE_PX = 12  # скобка-уголок мягкой зоны ловится в этом радиусе

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
        Сдвиг — «место для взгляда» идущему человеку: его показывает только точка, рамка стоит, где её поставили."""
        f = self._follower
        if f is None or not f.enabled or f.output is None:
            return None
        c = f.controller
        e = c.effort
        ax, ay = c.composition.anchor
        return (ax, ay, (e.dead_x, e.dead_y), (e.soft_x, e.soft_y), c.x.lead)

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
            changed = True
        r = self._result()
        key = (None if r is None else (r.seq, r.target_id, r.target_state), self.follow_zones())
        if key != self._an_seq:
            self._an_seq = key
            changed = True
        if changed:
            self.update()

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
        if self.mouse_mode:
            if e.button() == Qt.LeftButton:
                self.leftClicked.emit()
            elif e.button() == Qt.RightButton:
                self.rightClicked.emit()
            return
        if e.button() != Qt.LeftButton:
            return
        if self.click_guard is not None and not self.click_guard():
            return
        pos = e.position()
        hit = self._zone_hit(pos)
        if hit is None:
            self._select_at(pos)  # вне зоны — выбор человека сразу, как раньше
            return
        f = self._follower.controller
        scale = self.zone_scale_of() if self.zone_scale_of is not None else 1.0
        self._press = (pos, hit, f.composition.anchor, self._zone_rects()[0], scale)
        if hit != "move":
            self._drag = hit  # за уголок тянут сразу

    def mouseMoveEvent(self, e):  # noqa: N802
        pos = e.position()
        if self.mouse_mode:
            self._mouse_mode_move(pos)
            return
        if self._press is not None:
            p0, hit, (ax, ay), soft0, scale0 = self._press
            if self._drag is None and (pos - p0).manhattanLength() > self.DRAG_PX:
                self._drag = hit
            if self._drag is not None:
                r = self._image_rect()
                if self._drag == "move":
                    x, y, s = ax + (pos.x() - p0.x()) / r.width(), ay + (pos.y() - p0.y()) / r.height(), scale0
                else:  # уголок: зона растёт от своего центра одинаково по обеим осям
                    c = soft0.center()
                    k = (abs(pos.x() - c.x()) / (soft0.width() / 2) + abs(pos.y() - c.y()) / (soft0.height() / 2)) / 2
                    x, y, s = ax, ay, scale0 * k
                z = Zone.clamped(x, y, s)
                self.zoneChanged.emit(z.x, z.y, z.scale)
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
        if self.mouse_mode or e.button() != Qt.LeftButton:
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

    def leaveEvent(self, e):  # noqa: N802
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

    # --- отрисовка ------------------------------------------------------

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(C.BG))
        if self._image is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(self._image_rect(), self._image)
            self._paint_overlay(p)
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
        if self.mouse_mode:
            self._paint_mouse_mode(p)
        self._paint_zones(p)
        r = self._result()
        if r is None:
            return
        p.setRenderHint(QPainter.Antialiasing)
        fw = r.frame_size[0]
        k = self._image_rect().width() / fw
        for t in r.tracks:
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
