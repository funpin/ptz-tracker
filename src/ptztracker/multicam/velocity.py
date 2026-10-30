"""Смеситель скоростей: несколько источников (геймпады) управляют одной камерой."""

from __future__ import annotations

from typing import Any


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
        self._flush()

    def clear_all(self) -> None:
        """Всё в ноль без отправки (стоп уже ушёл); источники защёлкнуты до нулей."""
        self._latched.update(self._values)
        self._values.clear()
        self._sent = (0.0, 0.0, 0.0, 0.0)

    def _flush(self) -> None:
        new = self.total()
        old = self._sent
        if new[:2] != old[:2]:
            self._target.set_pan_tilt_velocity(new[0], new[1])
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
