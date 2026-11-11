"""«Позднее знание»: через сколько секунд камера узнаёт, что человек пошёл или встал, и успевает ответить.

За это время человек проходит v·T — столько регулятор держит идущего впереди точки, чтобы на остановке камера
докатилась до точки, а не проскочила (спека этапа 2, замеры площадки 2026-11-10).
"""

from __future__ import annotations

FRAME_EMA = 0.2  # доля нового интервала в среднем периоде замеров
FRAME_DT_START = 0.04  # до первых кадров — как 25 к/с
FRAME_GAP_S = 0.5  # интервал длиннее — это пауза (человека не было, поток встал), а не период кадров
FRAME_BURST_S = 0.01  # интервал короче — кадры пришли пачкой после затора: тоже не период
# Оценка скорости замечает старт по трём последним замерам (person_estimator.VEL_START_N): середина окна — один
# период назад; ещё полпериода в среднем кадр ждёт анализа
MEAS_PERIODS = 1.5


def late_knowledge(video_latency: float, meas_dt: float, camera_response: float) -> float:
    """T, секунды: задержка видео + ожидание и окно оценки скорости (в периодах замеров) + отклик камеры."""
    return video_latency + MEAS_PERIODS * meas_dt + camera_response


class FrameClock:
    """Как часто на самом деле приходят замеры: скользящее среднее интервалов между кадрами."""

    def __init__(self) -> None:
        self.meas_dt = FRAME_DT_START
        self._t: float | None = None

    def note(self, t_capture: float) -> None:
        if self._t is not None:
            dt = t_capture - self._t
            if dt <= 0:
                return  # тот же кадр
            if FRAME_BURST_S <= dt <= FRAME_GAP_S:
                self.meas_dt += FRAME_EMA * (dt - self.meas_dt)
        self._t = t_capture
