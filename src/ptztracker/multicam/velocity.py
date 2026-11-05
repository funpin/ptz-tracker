"""Смеситель скоростей: несколько источников (геймпады) управляют одной камерой."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))


class VelocityMixer:
    """Суммирует скорости источников и шлёт цели только изменившуюся сумму.

    Без потоков: вызывается из потока Qt.
    """

    def __init__(self, target: Any) -> None:
        self._target = target
        self._values: dict[str, tuple[float, float, float, float]] = {}
        self._latched: set[str] = set()
        self._sent: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        # множитель ручного поворота/наклона («медленнее на крупном плане»); слежение идёт мимо смесителя
        self.pt_scale: Callable[[], float] = lambda: 1.0
        self._sent_pt: tuple[float, float] = (0.0, 0.0)
        self._scale_warned = False

    def total(self) -> tuple[float, float, float, float]:
        sums = [0.0, 0.0, 0.0, 0.0]
        for vals in self._values.values():
            for i, v in enumerate(vals):
                sums[i] += v
        a, b, c, d = (_clamp(s) for s in sums)
        return (a, b, c, d)

    def set(
        self, source: str, pan: float = 0.0, tilt: float = 0.0,
        zoom: float = 0.0, focus: float = 0.0,
    ) -> None:
        vals = (float(pan), float(tilt), float(zoom), float(focus))
        if source in self._latched:
            if any(vals):
                return
            self._latched.discard(source)
        self._values[source] = vals
        self._flush()

    def release(self, source: str) -> None:
        self._values.pop(source, None)
        self._latched.discard(source)
        self._flush()

    def latch(self, source: str) -> None:
        """Источник держат с прежней камеры: ненулевые значения игнорируются, пока не придут нули."""
        self._values.pop(source, None)
        self._latched.add(source)
        self._flush()

    def clear_all(self) -> None:
        """Всё в ноль без отправки (стоп уже ушёл); удерживавшие источники защёлкнуты до нулей."""
        self._latched.update(n for n, v in self._values.items() if any(v))
        self._values.clear()
        self._sent = (0.0, 0.0, 0.0, 0.0)
        self._sent_pt = (0.0, 0.0)

    def _scaled_pt(self, pan: float, tilt: float) -> tuple[float, float]:
        if not (pan or tilt):
            return (0.0, 0.0)
        try:
            k = max(0.0, min(1.0, float(self.pt_scale())))
        except Exception as e:
            if not self._scale_warned:
                self._scale_warned = True
                log.debug("Множитель ручного поворота не посчитан: %s: %s", type(e).__name__, e)
            k = 1.0
        return (round(pan * k, 3) + 0.0, round(tilt * k, 3) + 0.0)

    def _send_pt(self, pt: tuple[float, float]) -> None:
        self._sent_pt = pt
        self._target.set_pan_tilt_velocity(*pt)

    def refresh(self) -> None:
        """Раз за такт окна: множитель мог измениться (зум), пока стик держат — шлём новую скорость."""
        pan, tilt = self._sent[:2]
        if pan or tilt:
            pt = self._scaled_pt(pan, tilt)
            if pt != self._sent_pt:
                self._send_pt(pt)

    def _flush(self) -> None:
        new = self.total()
        old = self._sent
        if new[:2] != old[:2]:
            self._send_pt(self._scaled_pt(new[0], new[1]))
        if new[2] != old[2]:
            self._target.set_zoom_velocity(new[2])
        if new[3] != old[3]:
            self._target.set_focus_velocity(new[3])
        self._sent = new

    def source(self, name: str) -> MixerSource:
        return MixerSource(self, name)


class MixerSource:
    """Вид смесителя для одного источника; остальное — к цели."""

    def __init__(self, mixer: VelocityMixer, name: str) -> None:
        self._mixer = mixer
        self._name = name
        self._cur = [0.0, 0.0, 0.0, 0.0]

    def _push(self) -> None:
        self._mixer.set(self._name, *self._cur)

    def set_pan_tilt_velocity(self, vx: float, vy: float) -> None:
        self._cur[0], self._cur[1] = vx, vy
        self._push()

    def set_zoom_velocity(self, v: float) -> None:
        self._cur[2] = v
        self._push()

    def set_focus_velocity(self, v: float) -> None:
        self._cur[3] = v
        self._push()

    def __getattr__(self, item: str) -> Any:
        return getattr(self._mixer._target, item)
