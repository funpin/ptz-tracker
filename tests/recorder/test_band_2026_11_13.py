"""Площадка 2026-11-13: выступает группа, Club_1 (HE40), MJPEG (2) 12 к/с, «Пояс · центр», зона ×0,8, зум на теле.

Оператор: «камера подёргивается», «при малейшем уходе артиста сразу уходит на общий план и стоит на нём минуту»,
«вообще ушла вниз». Запись — последние 5 минут до 17:14:16, без кадров (в кадре люди): только события. Разбор —
docs/field/2026-11-13-live.md.

Повтор — разомкнутый: камера в нём движется так, как двигалась в записи. Поэтому здесь проверяется только ответ
регулятора на записанные замеры; поведение целиком проверяют тесты стенда.
"""

from functools import lru_cache
from pathlib import Path

from ptztracker.recorder.reader import Episode
from ptztracker.recorder.replay import replay_controller

EPISODE = Path(__file__).resolve().parents[1] / "data" / "episodes" / "2026-11-13-club-band.zip"
CALM = (0.0, 150.0)  # артист стоит и играет; дальше — отъезды


@lru_cache(maxsize=1)
def replay():
    res = replay_controller(Episode.open(EPISODE))
    return res, [t - res.t[0] for t in res.t]


def window(codes, lo, hi):
    _, times = replay()
    return [c for t, c in zip(times, codes) if lo <= t < hi]


def crawls(codes, axis, lo, hi):
    """Движения оси, в которых код так и не поднялся выше ±1: камера тронулась на самой малой скорости и встала."""
    n = peak = 0
    for c in window(codes, lo, hi):
        k = abs(c[axis] - 50)
        if k:
            peak = max(peak, k)
        elif peak:
            n += peak == 1
            peak = 0
    return n


def test_camera_does_not_crawl_and_stop_while_musician_stands_and_plays():
    """В записи за 150 с камера 26 раз по повороту и 30 раз по наклону трогалась на коде ±1 и тут же вставала:
    поправка в полосе доводки лежала на пороге между «стоять» и самым малым кодом. На крупном плане это и было
    «подёргивание». Сколько раз камера трогается всего, повтор не показывает (в нём камера едет, как в записи) — это
    меряют стенд и живые прогоны (docs/field/2026-11-13-live.md)."""
    res, _ = replay()
    assert crawls(res.codes_rec, 0, *CALM) >= 20 and crawls(res.codes_rec, 1, *CALM) >= 20  # так было
    # остаются единичные такты кода ±1 (скорость идущего у самого порога кода) — не доводка: 2 и 6 на 150 с
    assert crawls(res.codes_new, 0, *CALM) <= 3
    assert crawls(res.codes_new, 1, *CALM) <= 8


def test_one_glitched_frame_does_not_send_zoom_wide_or_tilt_down():
    """230,3 с: на один кадр ключевые точки цели оказались внизу кадра (y 0,34 → 0,79 → 0,34). В записи через 0,1 с
    зум пошёл на отъезд кодом 1 (полная скорость), наклон — кодом 40. Окно — до 230,9 с: дальше замеры в записи уже
    испорчены самим отъездом."""
    res, _ = replay()
    rec = window(res.codes_rec, 230.2, 230.9)
    assert min(c[2] for c in rec) == 1 and min(c[1] for c in rec) <= 43  # так было
    new = window(res.codes_new, 230.2, 230.9)
    assert all(c[2] == 50 for c in new), sorted({c[2] for c in new})  # один кадр зум не трогает
    assert all(abs(c[1] - 50) <= 3 for c in new), sorted({c[1] for c in new})


def test_target_lost_in_mid_frame_is_not_chased():
    """231,4 с: цель пропала посреди кадра (её потерял сам быстрый отъезд). В записи камера ещё 5 с шла вправо и вниз
    кодами 63 и 40 и отъезжала кодом 1 — «ушла вниз» на общем плане. Пропавшего посреди кадра не ищем: он на месте."""
    res, times = replay()
    idle = [c for t, c, s in zip(times, res.codes_new, res.state) if 231.4 <= t < 240 and s == "idle"]
    was = [c for t, c, s in zip(times, res.codes_rec, res.state) if 231.4 <= t < 240 and s == "idle"]
    assert len(idle) > 50
    assert any(c[1] <= 40 for c in was) and any(c[2] == 1 for c in was)  # так было
    assert all(c[2] >= 50 for c in idle), sorted({c[2] for c in idle})  # ни одного такта отъезда
    assert sum(1 for c in idle if c[0] != 50 or c[1] != 50) <= 12  # поворот и наклон — только остановка того, что ехало


def test_frames_arriving_in_a_burst_do_not_zoom_out():
    """152,4 с: после затора в 0,4 с кадры пришли пачкой, скорость артиста насчиталась в разы больше настоящей, и
    прогноз «уйдёт из кадра» дал отъезд кодом 10. Артист был в 0,64 ширины кадра — до края ещё четверть кадра."""
    res, _ = replay()
    assert min(c[2] for c in window(res.codes_rec, 152.0, 153.6)) <= 16  # так было
    new = window(res.codes_new, 152.0, 153.6)
    assert all(c[2] >= 50 for c in new), sorted({c[2] for c in new})
