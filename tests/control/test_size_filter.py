"""Размер человека для зума: растёт сразу, а «сжаться» быстрее, чем человек может отойти, ему не дают (этап 2).

Живой прогон 2026-11-10: человек встал боком и наклонился — мерка «нос → плечи» за 2,5 с упала с 0,235 до 0,035,
регулятор принял это за «человек далеко» и наехал; человек выпрямился — отъехал. Это и есть «насос» зума.
"""

import math

from ptztracker.control.size_filter import U_FALL_CONFIRM_S, U_FALL_RATE, SizeFilter

DT = 1 / 25


def feed(fn, T, f=None):
    f = f or SizeFilter()
    out = []
    for i in range(round(T / DT)):
        t = i * DT
        out.append((t, f.update(t, fn(t))))
    return out


def test_size_filter_ignores_short_collapse():
    # 0,235 → 0,035 на 2,5 с (встал боком и наклонился): выход падает не больше чем на 35 % и возвращается сразу
    rows = feed(lambda t: 0.035 if 4.0 <= t < 6.5 else 0.235, 10.0)
    assert min(u for _, u in rows) >= 0.65 * 0.235
    assert abs(next(u for t, u in rows if t >= 7.0) - 0.235) <= 0.01


def test_size_filter_follows_growth_at_once():
    # человек подошёл (или камера наехала): размер растёт без задержки — иначе зум перелетал бы
    rows = feed(lambda t: 0.1 if t < 2.0 else 0.2, 4.0)
    assert next(u for t, u in rows if t >= 2.5) >= 0.195


def test_size_filter_follows_person_walking_away():
    # человек уходит от камеры: размер убывает на 7 % в секунду — фильтр идёт за ним без отставания
    rows = feed(lambda t: 0.2 * math.exp(-0.07 * t), 10.0)
    assert all(abs(math.log(u / (0.2 * math.exp(-0.07 * t)))) <= 0.03 for t, u in rows if t > 1.0)
    assert 0.07 < U_FALL_RATE  # предел спада — быстрее самой быстрой ходьбы от камеры


def test_size_filter_accepts_lasting_change():
    # спад держится дольше U_FALL_CONFIRM_S — это не поворот корпуса: размер действительно другой
    rows = feed(lambda t: 0.2 if t < 2.0 else 0.08, 2.0 + U_FALL_CONFIRM_S + 3.0)
    assert next(u for t, u in rows if t >= 2.0 + U_FALL_CONFIRM_S - 0.2) >= 0.08 * 1.3  # ещё не верим
    assert abs(rows[-1][1] - 0.08) <= 0.01  # поверили


def test_size_filter_reset_forgets():
    f = SizeFilter()
    feed(lambda t: 0.2, 1.0, f)
    f.reset()
    assert f.update(5.0, 0.05) == 0.05
