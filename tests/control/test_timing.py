"""«Позднее знание» T: через сколько секунд камера узнаёт, что человек пошёл или встал (спека этапа 2)."""

from loop import run

from ptztracker.camera import he42
from ptztracker.control.camera_model import Calibration, default_calibration
from ptztracker.control.timing import FrameClock, late_knowledge


def test_late_knowledge_matches_field_numbers():
    # площадка 2026-11-10: MJPEG (1) — видео 0,15 с, 25 к/с; MJPEG (2) — 0,25 с, 12 к/с; камера отвечает за 0,09 с
    assert abs(late_knowledge(0.15, 1 / 25, 0.09) - 0.30) <= 0.01
    assert abs(late_knowledge(0.25, 1 / 12, 0.09) - 0.465) <= 0.01


def test_frame_clock_tracks_frame_rate():
    clock = FrameClock()
    assert clock.meas_dt == 0.04  # до первых кадров — как 25 к/с
    for i in range(60):
        clock.note(i / 12)
    assert abs(clock.meas_dt - 1 / 12) < 0.003
    clock.note(60 / 12 + 2.0)  # человека не было 2 с: пауза — не период кадров
    assert abs(clock.meas_dt - 1 / 12) < 0.003
    clock.note(60 / 12 + 2.0)  # тот же кадр дважды не считается
    assert abs(clock.meas_dt - 1 / 12) < 0.003
    for i in range(1, 60):
        clock.note(7.0 + i / 25)
    assert abs(clock.meas_dt - 1 / 25) < 0.003


def test_late_knowledge_survives_irregular_frames():
    # две камеры делят нейросеть: кадры приходят с разбросом до 0,05 с — оценка T не скачет больше чем на 20 %
    seen = []
    run(lambda t: 0.5, T=10.0, latency=0.15, meas_dt=1 / 15, meas_jitter=0.05,
        controller_hook=lambda t, ctrl, out: seen.append((t, ctrl.t_late)))
    late = [v for t, v in seen if t > 2.0]
    calib = default_calibration()
    assert max(late) <= 1.2 * min(late)
    assert abs(sorted(late)[len(late) // 2] - late_knowledge(0.15, 1 / 15, calib.dead_time + calib.tau)) < 0.03


def test_calibration_without_wizard_takes_response_from_model():
    he40, he42_ = default_calibration(model="AW-HE40"), default_calibration(model="AW-HE42")
    assert (he40.dead_time, he40.tau) == (he42.HE40_DEAD_TIME_S, he42.HE40_RAMP_TAU_S) == (0.05, 0.05)
    assert (he42_.dead_time, he42_.tau) == (he42.DEAD_TIME_S, he42.RAMP_TAU_S) == (0.08, 0.12)
    unknown = default_calibration()
    assert (unknown.dead_time, unknown.tau) == (0.08, 0.12)  # модель не названа — как HE42
    # задержка видео по умолчанию — прямой замер 2026-11-10; H.264 не трогали (замер шумный)
    assert (he40.latency_for("mjpeg"), he40.latency_for("mjpeg_2"), he40.latency_for("h264")) == (0.15, 0.25, 0.6)
    # что намерил мастер, остаётся как есть
    wizard = Calibration.from_json({**he40.to_json(), "dead_time": 0.11, "tau": 0.2, "video_latency": {"mjpeg": 0.079}})
    assert (wizard.dead_time, wizard.tau, wizard.latency_for("mjpeg")) == (0.11, 0.2, 0.079)


def test_frame_clock_ignores_burst_after_a_stall():
    # поток встал на 0,6 с и отдал три кадра пачкой: ни пауза, ни интервалы в миллисекунды — не период кадров
    clock = FrameClock()
    for i in range(50):
        clock.note(i / 25)
    before = clock.meas_dt
    t = 50 / 25 + 0.6
    for k in range(3):
        clock.note(t + 0.001 * k)
    assert abs(clock.meas_dt - before) <= 0.1 * before


def test_controller_gives_watchdog_its_own_thresholds():
    """Сторож получает свои пороги (EffortProfile.watch_*), а не границу и покой зоны: проверка на «Резко»."""
    from loop import run

    from ptztracker.control import framing as F

    seen = []
    orig = F.SwingWatch.update

    def spy(self, now, v_cmd, err, soft, zoom_feed=0.0, target=None, dead=0.0):
        seen.append((round(soft, 3), round(dead, 3)))
        return orig(self, now, v_cmd, err, soft, zoom_feed, target, dead)

    F.SwingWatch.update = spy
    try:
        run(lambda t: 0.5, T=1.0, effort=1.0, zones={})
    finally:
        F.SwingWatch.update = orig
    assert (0.18, 0.05) in seen  # поворот: граница сторожа прежняя 0,18 (зона теперь 0,22), покой — 0,05 (был 0,03)
