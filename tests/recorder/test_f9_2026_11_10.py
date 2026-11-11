"""Запись F9 оператора от 2026-11-10: симулятор, «Пояс · центр», «Обычно», случайный маршрут 906744089.

В записи: 8 циклов зума «отъехал в 2–4 раза — вернулся» за 113 с, три проскока на 0,4 кадра и два «нырка» наклона,
когда человек был срезан краем кадра. Кадры не хранятся (7 МБ) — только события: повтору регулятора их хватает.

Повтор — разомкнутый: камера в нём движется так, как двигалась в записи. Поэтому здесь проверяется только то, что видно
по ответу регулятора на записанные замеры; поведение целиком проверяют тесты стенда (tests/control/test_operator_eye.py).
"""

from pathlib import Path

from ptztracker.recorder.reader import Episode
from ptztracker.recorder.replay import replay_controller

EPISODE = Path(__file__).resolve().parents[1] / "data" / "episodes" / "2026-11-10-sim-close-up.zip"


BRAKE_S = 0.3  # столько наклон ещё останавливается после первого ненадёжного замера


def test_tilt_does_not_dive_when_person_is_cut_by_frame_edge():
    """41-я, 72-я и 112-я секунды: видно одно плечо, голова прикинута по рамке на треть кадра ниже — в записи наклон шёл
    вниз кодами 42–45 по полсекунды. Ненадёжному замеру по вертикали не верим: наклон останавливается и стоит."""
    ep = Episode.open(EPISODE)
    unsure = {round(float(e["t"]), 6) for e in ep.events("control") if e["measure"] and not e["measure"]["confident"]}
    res = replay_controller(ep)
    rec, new, start, prev = [], [], None, None
    for t, was, now in zip(res.t, res.codes_rec, res.codes_new):
        if round(t, 6) not in unsure:
            continue
        if prev is None or t - prev > 0.1:
            start = t  # новая серия ненадёжных замеров
        prev = t
        if t - start >= BRAKE_S:
            rec.append(was[1])
            new.append(now[1])
    assert sum(1 for c in rec if c <= 45) >= 20, "в записи камера ныряла"
    assert all(49 <= c <= 51 for c in new), new


def _zoom_turns(codes) -> int:
    """Сколько раз зум сменил направление (наезд ↔ отъезд)."""
    signs = [1 if c[2] > 50 else -1 for c in codes if c[2] != 50]
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


def test_zoom_does_not_turn_more_than_once_a_minute():
    """В записи зум за 113 с 20 раз сменил направление и ехал 71 % времени («отъехал — наехал»). На те же замеры
    регулятор этапа 2 отвечает спокойно: направление зума меняется не чаще раза в минуту, едет он меньше четверти времени."""
    res = replay_controller(Episode.open(EPISODE))
    minutes = (res.t[-1] - res.t[0]) / 60
    assert _zoom_turns(res.codes_rec) >= 15, "в записи зум качал"
    assert _zoom_turns(res.codes_new) <= minutes, _zoom_turns(res.codes_new)
    assert sum(1 for c in res.codes_new if c[2] != 50) / len(res.codes_new) <= 0.25
