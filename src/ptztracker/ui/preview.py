from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from ptztracker.video.frames import FrameSource


class PreviewWidget(QWidget):
    """Превью последнего кадра камеры с сохранением пропорций."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._source: FrameSource | None = None
        self._image: QImage | None = None
        self._seq = -1
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_source(self, source: FrameSource | None) -> None:
        self._source = source
        self._image = None
        self._seq = -1
        self.update()

    def has_frame(self) -> bool:
        return self._image is not None

    def _tick(self) -> None:
        fr = self._source.latest() if self._source else None
        if fr is None:
            if self._image is not None:
                self._image = None
                self.update()
            return
        if fr.seq != self._seq:
            h, w, _ = fr.image.shape
            # .copy(): QImage не владеет буфером numpy
            self._image = QImage(fr.image.data, w, h, fr.image.strides[0], QImage.Format_BGR888).copy()
            self._seq = fr.seq
            self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(18, 20, 26))
        if self._image is not None:
            iw, ih = self._image.width(), self._image.height()
            scale = min(self.width() / iw, self.height() / ih)
            w, h = iw * scale, ih * scale
            target = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(target, self._image)
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
