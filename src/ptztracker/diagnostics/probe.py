"""Замер скорости поворота по картинке: общий для диагностики и мастера калибровки."""

from __future__ import annotations

import logging
import time

from ptztracker.camera import protocol as P
from ptztracker.camera.client import CameraClient
from ptztracker.diagnostics.motion import measure_shift
from ptztracker.video.frames import FrameSource

PACE_S = 0.15  # пауза между командами при замерах (с запасом к 130 мс)
SPEED_MAX_S = 1.5
SPEED_MAX_FRACTION = 0.25

log = logging.getLogger(__name__)


class Pacer:
    """Отправка команд не чаще PACE_S."""

    def __init__(self, client: CameraClient, gap: float = PACE_S):
        self.client, self.gap, self._last = client, gap, 0.0

    def send(self, cmd: str) -> str:
        wait = self._last + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        return self.client.send(cmd)

    def try_send(self, cmd: str) -> str | None:
        try:
            return self.send(cmd)
        except Exception as e:
            log.warning("%s: %s", cmd, e)
            return None


def probe_run(
    pacer: Pacer,
    src: FrameSource,
    code: int,
    max_s: float = SPEED_MAX_S,
    max_fraction: float = SPEED_MAX_FRACTION,
) -> dict:
    """Повернуть с кодом code до 25% ширины кадра или 1.5 с; вернуть скорость в px/с, разгон и торможение."""
    first = src.latest()
    width = first.image.shape[1]
    prev, last_seq = first, first.seq
    cum = 0.0
    samples = [(0.0, 0.0)]
    t_cmd = time.monotonic()
    pacer.send(P.pan_tilt_speed(code, 50))
    while time.monotonic() - t_cmd < max_s and abs(cum) < max_fraction * width:
        fr = src.latest()
        if fr is not None and fr.seq != last_seq:
            dx, _ = measure_shift(prev.image, fr.image)
            cum += dx
            samples.append((time.monotonic() - t_cmd, cum))
            prev, last_seq = fr, fr.seq
        time.sleep(0.005)
    t_stop = time.monotonic()
    pacer.send(P.STOP_PT)
    decel_s = None
    quiet_since = None
    while time.monotonic() - t_stop < 2.0:
        fr = src.latest()
        if fr is not None and fr.seq != last_seq:
            dx, _ = measure_shift(prev.image, fr.image)
            prev, last_seq = fr, fr.seq
            if abs(dx) < 0.5:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since > 0.2:
                    decel_s = round(quiet_since - t_stop, 3)
                    break
            else:
                quiet_since = None
        time.sleep(0.005)
    # скорость — по второй половине разгона (установившийся участок)
    tail = samples[len(samples) // 2 :]
    if len(tail) >= 2 and tail[-1][0] > tail[0][0]:
        v = (tail[-1][1] - tail[0][1]) / (tail[-1][0] - tail[0][0])
    else:
        v = 0.0
    accel_s = None
    if v:
        for i in range(1, len(samples)):
            t0, c0 = samples[i - 1]
            t1, c1 = samples[i]
            if t1 > t0 and abs((c1 - c0) / (t1 - t0)) >= 0.9 * abs(v):
                accel_s = round(t1, 3)
                break
    return {
        "code": code,
        "px_per_s": round(-v, 1),  # поворот вправо сдвигает картинку влево
        "frame_width": width,
        "accel_s": accel_s,
        "decel_s": decel_s,
        "moved_px": round(-cum, 1),
    }
