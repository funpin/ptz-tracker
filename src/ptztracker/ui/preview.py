from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

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

TARGET_COLOR = QColor(59, 130, 246)
LOST_COLOR = QColor(245, 158, 11)
OTHER_COLOR = QColor(255, 255, 255, 170)
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._source: FrameSource | None = None
        self._analyzer = None
        self._image: QImage | None = None
        self._seq = -1
        self._an_seq = None
        self.show_skeleton = False
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
        key = None if r is None else (r.seq, r.target_id, r.target_state)
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

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() != Qt.LeftButton or self._analyzer is None:
            return
        r = self._result()
        fp = self.widget_to_frame(e.position())
        if fp is None or r is None:
            return
        # кадр анализа может отличаться размером от показанного — пересчитать
        k = r.frame_size[0] / self._image.width()
        tid = self._analyzer.select_at(fp[0] * k, fp[1] * k)
        if tid is not None:
            self.targetClicked.emit(tid)
            self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        self.setCursor(Qt.PointingHandCursor if self._seen_track_at(e.position()) else Qt.ArrowCursor)

    # --- отрисовка ------------------------------------------------------

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(18, 20, 26))
        if self._image is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(self._image_rect(), self._image)
            self._paint_overlay(p)
            return
        p.setPen(QColor(200, 205, 215))
        f = QFont()
        f.setPointSize(18)
        p.setFont(f)
        text = "Нет видео"
        if self._source is not None:
            err = self._source.stats().error
            if err:
                text += "\n" + err
        else:
            text = "Камера не выбрана"
        p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, text)

    def _paint_overlay(self, p: QPainter) -> None:
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
                p.setPen(QPen(color, 3))
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect)
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
        f = QFont()
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
        p.setPen(QColor(255, 255, 255))
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
