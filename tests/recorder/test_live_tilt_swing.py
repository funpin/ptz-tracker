"""Раскачка наклона на записи живой AW-HE42 (площадка 2026-10-29, прогон «4б», последние 15 с слежения).

На 20,5–24 с, сразу после наезда зума (#Z79, зум ~0xA8E), наклон закачался: коды 23↔75, ошибка по y до ±0,58,
в 24,2 с сторож выключил слежение. Эпизод записан со старой таблицей скоростей (одна на все зумы, в долях
кадра): камера по вертикали ехала в несколько раз быстрее таблицы, а #APC в эти секунды не отвечал (поправке
скорости нечего было мерить). Кадров в эпизоде нет — они тут не нужны.

Повтор идёт «разомкнуто»: ошибки записаны с тем поведением камеры, а новые коды на них не влияют. Поэтому здесь
проверяется только то, что регулятор с нынешней таблицей (°/с, tilt берёт поправку поворота) на тех же ошибках
командует наклон заметно мягче (сравнивается размер кодов наклона). Это НЕ доказательство, что раскачка на живой
камере ушла: замкнутый контур — в tests/control/test_speed_adapt.py, а живое подтверждение — повтор на площадке.
"""

from pathlib import Path

from ptztracker.camera.session import UNCALIBRATED_SAFETY
from ptztracker.control.camera_model import default_calibration
from ptztracker.recorder.reader import Episode
from ptztracker.recorder.replay import replay_controller

EPISODE = Path(__file__).resolve().parents[1] / "data" / "episodes" / "2026-10-29-r4d-sharp.zip"
SWING_FROM_S = 20.4  # секунд от 72900 монотонных часов записи: после наезда зума начинается раскачка


def _swing_tilt_amplitude(res):
    swing = [i for i, t in enumerate(res.t) if t - 72900 >= SWING_FROM_S]
    return max(abs(res.codes_new[i][1] - 50) for i in swing), max(abs(res.codes_rec[i][1] - 50) for i in swing)


def test_episode_is_small_and_has_the_swing():
    assert EPISODE.stat().st_size <= 1024 * 1024
    res = replay_controller(Episode.open(EPISODE))
    assert res.calibration_ok and res.summary().skipped_events == 0
    swing_err = [abs(e) for t, e in zip(res.t, res.err_y) if t - 72900 >= SWING_FROM_S]
    assert max(swing_err) > 0.5  # раскачка в записи: ошибка по y до ±0,58
    assert max(abs(c[1] - 50) for c in res.codes_rec) >= 25  # коды 23↔75


def test_recorded_table_reproduces_swing_codes():
    """С записанной калибровкой (старая таблица) повтор даёт те же размашистые коды наклона, что были."""
    new, rec = _swing_tilt_amplitude(replay_controller(Episode.open(EPISODE)))
    assert new >= 25 and abs(new - rec) <= 1, (new, rec)


def test_deg_table_commands_tilt_softer_on_the_same_errors():
    """Таблица в °/с: на тех же ошибках размах кодов наклона заметно меньше записанного (27 → 17)."""
    ep = Episode.open(EPISODE)
    res = replay_controller(ep, calib=default_calibration(safety=UNCALIBRATED_SAFETY))
    new, rec = _swing_tilt_amplitude(res)
    assert new <= 0.75 * rec, (new, rec)
    # спокойная часть (до наезда зума): наклон не дёргается — ни одного разворота кодов
    quiet = [res.codes_new[i][1] for i, t in enumerate(res.t) if t - 72900 < SWING_FROM_S]
    signs = [1 if c > 50 else -1 for c in quiet if c != 50]
    assert sum(a != b for a, b in zip(signs, signs[1:])) == 0
