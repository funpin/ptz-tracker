import socket
import time

import cv2
import numpy as np

from ptztracker.diagnostics.motion import measure_shift
from ptztracker.diagnostics.notify import NotificationListener
from ptztracker.sim.scene import SyntheticStage


def test_measure_shift_known_translation():
    img = SyntheticStage().frame_at(0)[:720, :1280]
    m = np.float32([[1, 0, -37], [0, 1, 12]])
    b = cv2.warpAffine(img, m, (1280, 720))
    dx, dy = measure_shift(img, b)
    assert abs(dx + 37) < 1.5 and abs(dy - 12) < 1.5


def test_measure_shift_identical_is_zero():
    img = SyntheticStage().frame_at(0)[:720, :1280]
    dx, dy = measure_shift(img, img.copy())
    assert abs(dx) < 0.3 and abs(dy) < 0.3


def test_notification_listener_records_bytes():
    ln = NotificationListener(0)
    ln.start()
    try:
        with socket.create_connection(("127.0.0.1", ln.port)) as s:
            s.sendall(b"hello-camera")
        deadline = time.monotonic() + 2
        while not ln.events() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert b"".join(d for _, d in ln.events()) == b"hello-camera"
    finally:
        ln.stop()
