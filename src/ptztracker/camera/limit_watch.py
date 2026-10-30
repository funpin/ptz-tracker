"""Упор камеры: геймпад давит, а положение не меняется — пора дать оператору толчок в руки.

Свой поток: пока спрос нулевой — спит и камеру не опрашивает. Запросы блокирующие,
поэтому только отсюда, не из потока Qt.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ptztracker.camera import protocol as P

log = logging.getLogger(__name__)

DEADZONE = 0.02  # как в camera/speed.py velocity_to_code: меньше — камере команда «стоять»
# На самых медленных кодах камера за stuck_s сдвигается меньше min_units — не отличить от упора.
MIN_PT_JUDGE = 0.1


class LimitWatch:
    def __init__(
        self,
        query_pan_tilt: Callable[[], tuple[int, int] | None],
        query_zoom: Callable[[], int | None],
        on_limit: Callable[[], None],
        period_s: float = 0.2,
        stuck_s: float = 0.3,
        min_units: int = 2,
        pulse_s: float = 0.7,
    ) -> None:
        self._qpt, self._qz, self._on_limit = query_pan_tilt, query_zoom, on_limit
        self.period_s, self.stuck_s, self.min_units, self.pulse_s = period_s, stuck_s, min_units, pulse_s
        self._demand = (0.0, 0.0, 0.0)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._since: dict[str, tuple[int, float]] = {}  # ось -> (знак спроса, когда начался)
        self._anchors: dict[str, tuple[float, int]] = {}
        self._last_pulse = float("-inf")
        self._thread = threading.Thread(target=self._run, name="limit-watch", daemon=True)
        self._thread.start()

    def set_demand(self, vx: float, vy: float, vz: float) -> None:
        d = tuple(0.0 if abs(float(v)) < DEADZONE else float(v) for v in (vx, vy, vz))
        now = time.monotonic()
        for axis, v in zip(("pan", "tilt", "zoom"), d):
            sign = (v > 0) - (v < 0)
            cur = self._since.get(axis)
            if cur is None or cur[0] != sign:
                self._since[axis] = (sign, now)
                self._anchors.pop(axis, None)
        self._demand = d  # type: ignore[assignment]
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=0.5)

    def _run(self) -> None:
        while not self._stop.is_set():
            if not any(self._demand):
                self._anchors.clear()
                self._wake.wait()
                self._wake.clear()
                continue
            try:
                self._tick()
            except Exception:
                log.debug("Опрос упора не удался", exc_info=True)
            self._stop.wait(self.period_s)

    def _check(self, axis: str, value: int | None, now: float) -> bool:
        if value is None:
            return False
        since = self._since.get(axis)
        if since is None or now - since[1] < self.stuck_s + self.period_s:
            anchor = self._anchors.get(axis)
            if anchor is None or abs(value - anchor[1]) > self.min_units:
                self._anchors[axis] = (now, value)
            return False
        anchor = self._anchors.get(axis)
        if anchor is None or abs(value - anchor[1]) > self.min_units:
            self._anchors[axis] = (now, value)
            return False
        return now - anchor[0] >= self.stuck_s

    def _tick(self) -> None:
        vx, vy, vz = self._demand
        if abs(vx) < MIN_PT_JUDGE:
            vx = 0.0
        if abs(vy) < MIN_PT_JUDGE:
            vy = 0.0
        now = time.monotonic()
        stuck = False
        if vx or vy:
            pt = self._qpt()
            if pt is not None:
                stuck |= bool(vx) and self._check("pan", int(pt[0]), now)
                stuck |= bool(vy) and self._check("tilt", int(pt[1]), now)
        else:
            self._anchors.pop("pan", None)
            self._anchors.pop("tilt", None)
        if vz:
            z = self._qz()
            if z is not None:
                if (vz > 0 and z >= P.LENS_MAX) or (vz < 0 and z <= P.LENS_MIN):
                    stuck = True
                else:
                    stuck |= self._check("zoom", int(z), now)
        else:
            self._anchors.pop("zoom", None)
        if stuck and now - self._last_pulse >= self.pulse_s and not self._stop.is_set():
            self._last_pulse = now
            try:
                self._on_limit()
            except Exception:
                log.debug("on_limit упал", exc_info=True)
