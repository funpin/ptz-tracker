"""Сохранённый эпизод: 30 с слежения на симуляторе, записанные чёрным ящиком (шаг А).

Проверяет, что повтор по готовому файлу даёт те же коды и разумную сводку. Эпизод лежит в репозитории,
поэтому тест не зависит от скорости компьютера. Новые плохие эпизоды с площадки кладите рядом.

После намеренного изменения регулятора эпизод перезаписывают: `.venv/bin/python tools/record_episode.py`
(повтор старой записи новым регулятором честно расходится — тест при этом не ослабляют).
"""

from pathlib import Path

import pytest

from ptztracker.recorder.reader import Episode
from ptztracker.recorder.replay import replay_controller

EPISODE = Path(__file__).resolve().parents[1] / "data" / "episodes" / "sim_walk_30s.zip"


@pytest.mark.slow
def test_stored_sim_episode_replays_same_codes():
    assert EPISODE.stat().st_size <= 3 * 1024 * 1024
    res = replay_controller(Episode.open(EPISODE))
    s = res.summary()
    assert res.calibration_ok and not s.partial_start and s.skipped_events == 0
    assert 25 <= s.following_s <= 35 and s.steps > 500
    assert len(res.codes_rec) == len(res.codes_new) == s.steps
    for rec, new in zip(res.codes_rec, res.codes_new):
        assert all(abs(a - b) <= 1 for a, b in zip(rec, new)), (rec, new)
    assert s.max_code_diff <= 1
    assert s.pan_reversals_new <= s.pan_reversals_rec + 1
    # ускорение камеры (модель разгона); догон уходящего за край — по записи с запасом ~9% (τ 0,12 с с 2026-10-29; физический потолок v_max/τ = 10 — далеко)
    assert s.max_accel_new <= 5.7  # в записи 5,22 fw/s² (при τ 0,37 было 3,16)
    assert 0 <= s.out_of_soft_pct <= 50
    assert s.target_switches == 0
