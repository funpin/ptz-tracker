"""Раскачка поворота на теле: живая AW-HE40 в клубе (2026-10-30, `--lab follow`, «Пояс · центр», 60 с, свежая калибровка).

Человек далеко, «Пояс» за 25 с довёл зум до 0xF16–0xF41 (угол ~3°). Там HE40 сама замедляет поворот (код 70 —
2,5°/с вместо 13°/с; §7 спеки). С 29 с регулятор погнал зум назад (#Z01…#Z23), а поворот закачался 34↔69 раз
в ~1,5 с. Опрос положения в эти секунды стоял: ответов #APC/#GZ не было 10 с (запросы ждали в очереди до 7 с,
команды движения их вытесняли), и регулятор считал скорости по зуму 0xF41, хотя камера уже отъехала к 0xA38.

Тест держит факты записи — по ним будет проверяться исправление опроса положения при активном слежении
(BACKLOG). Кадров в эпизоде нет — они тут не нужны.
"""

from pathlib import Path

from ptztracker.recorder.reader import Episode
from ptztracker.recorder.replay import replay_controller

EPISODE = Path(__file__).resolve().parents[1] / "data" / "episodes" / "2026-10-30-he40-tele.zip"
SWING = (29.0, 36.0)  # секунд от первого такта регулятора


def _rel(res):
    return [t - res.t[0] for t in res.t]


def test_episode_is_small_and_replays():
    assert EPISODE.stat().st_size <= 1024 * 1024
    res = replay_controller(Episode.open(EPISODE))
    s = res.summary()
    assert res.calibration_ok and s.skipped_events == 0 and s.max_code_diff == 0
    assert 55 <= s.following_s <= 65 and s.target_switches == 0


def test_pan_swings_on_tele_while_position_is_stale():
    ep = Episode.open(EPISODE)
    res = replay_controller(ep)
    rel = _rel(res)
    swing = [i for i, t in enumerate(rel) if SWING[0] <= t <= SWING[1]]
    pans = [res.codes_rec[i][0] for i in swing]
    assert min(pans) <= 36 and max(pans) >= 66  # поворот 34↔69
    # регулятор всё это время думает, что зум на теле
    assert all(res.zoom_pos[i] >= 0xF00 for i in swing)
    # а положение камеры не приходило ≥ 9 с, хотя зум в это время гнали назад
    t0 = res.t[0]
    pos_t = sorted(e["t"] - t0 for e in ep.events() if e.get("kind") == "position" and e["t"] >= t0)
    gap = max(b - a for a, b in zip(pos_t, pos_t[1:]))
    assert gap >= 9.0, gap
    zoom_out = [
        e for e in ep.events()
        if e.get("kind") == "cmd" and e.get("cmd", "").startswith("#Z") and int(e["cmd"][2:]) < 30
        and SWING[0] <= e["t"] - t0 <= SWING[1]
    ]
    assert len(zoom_out) >= 3  # #Z01, #Z12, #Z21…
