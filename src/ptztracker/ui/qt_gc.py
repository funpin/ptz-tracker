"""Уборка памяти Python только в потоке Qt.

Автоматическая уборка идёт в том потоке, который создал очередной объект. Если это
фоновый поток, а среди мусора есть объекты Qt, он берёт замок сигналов Qt и ждёт GIL,
пока поток Qt держит GIL и ждёт тот же замок: окно замирает насовсем (см. DECISIONS).
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QTimer


class MainThreadGc(QObject):
    def __init__(self, parent: QObject, interval_ms: int = 1000) -> None:
        super().__init__(parent)  # живёт, пока жив родитель: ссылку на него хранить не нужно
        self._limits = gc.get_threshold()
        self._was_enabled = gc.isenabled()
        gc.disable()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check)
        self._timer.start(interval_ms)

    def check(self) -> None:
        """Те же пороги, что у автоматической уборки: старшее поколение — первым."""
        counts = gc.get_count()
        for generation in (2, 1, 0):
            if counts[generation] > self._limits[generation]:
                gc.collect(generation)
                return

    def stop(self) -> None:
        self._timer.stop()
        if self._was_enabled:
            gc.enable()
