"""Закон оси этапа 2: идущего камера держит впереди точки, встаёт у точки, не выпускает из зоны (спека 2026-11-10).

Стенд — как в целях слежения (камера как HE42 через настоящий планировщик, шум замеров), поток MJPEG (1): видео
опаздывает на 0,15 с, замеры 25 раз в секунду. «Пояс», обычная зона.

Пороги — по замерам 2026-11-11 (таблица в конце плана этапа 2), а не по первым прикидкам плана: стенд показал, что
от первого шага человека до ответа камеры проходит 0,6–0,8 с (заметить, что пошёл, — 0,45; команда и камера — 0,35),
и за это время человек на быстром шаге уходит за границу обычной зоны, что бы ни делал регулятор.
"""

import pytest
from loop import run
from metrics import (
    moving_share,
    off_frame_share,
    overshoot_after,
    overtake_max,
    reversals,
    settle_time,
    zone_exit_depth,
)
from test_operator_eye import BENCH as EYE_BENCH
from test_operator_eye import FRAME_M, avg
from test_operator_eye import SEEDS as ROUTES
from test_tracking_goals import BENCH, SEEDS, snap_walk, stand, walk

from ptztracker.control import framing
from ptztracker.sim import person_motion as pm

MJPEG1 = dict(latency=0.15, meas_dt=1 / 25)
START, STOP = 3.0, 12.0
ZONE = 0.22  # граница обычной зоны по горизонтали


def trials(fn, T, **kw):
    return [run(fn, T=T, seed=seed, **BENCH, **MJPEG1, **kw) for seed in SEEDS]


def watched(fn, T, seed=0, **kw):
    """Прогон и то, что решала ось поворота каждый такт: (t, запас, скорость для камеры, разрешённое ускорение, T)."""
    rows = []
    tr = run(fn, T=T, seed=seed, **BENCH, **MJPEG1, **kw,
             controller_hook=lambda t, c, out: rows.append((t, c.x.lead_now, c.x.v_follow, c.x.a_lim, c.t_late)))
    return tr, rows


@pytest.mark.parametrize("direction", [1, -1])
def test_walker_is_held_ahead_of_mark(direction):
    """Ровная ходьба 0,35 кадра/с: человек впереди точки (в сторону хода) на 0,10…0,14 кадра — «как на макете»
    (решение оператора 2026-11-11; с малым запасом было 0,09)."""
    for tr in trials(walk(direction * 0.35, t0=START, t1=STOP), STOP):
        ahead = [direction * (x - 0.5) for t, x in zip(tr.t, tr.img_x) if START + 3 <= t <= START + 6]
        assert 0.10 <= avg(ahead) <= 0.14, avg(ahead)


def test_lead_is_what_camera_travels_while_it_learns_of_a_stop():
    """Запас впереди точки — LEAD·v·T: растёт со скоростью шага и не больше LEAD_ZONE_SHARE границы зоны."""
    leads = {}
    for v in (0.2, 0.35, 0.7):
        tr, rows = watched(walk(v, t0=START, t1=STOP), STOP)
        cruise = [r for r in rows if START + 3 <= r[0] <= START + 6]
        leads[v] = avg(r[1] for r in cruise)
        assert all(abs(lead - framing.LEAD * v_cam * t_late) < 1e-9 or abs(lead) == framing.LEAD_ZONE_SHARE * ZONE
                   for _, lead, v_cam, _, t_late in cruise)
        assert abs(cruise[0][4] - (0.15 + 1.5 / 25 + 0.08 + 0.12)) <= 0.02  # видео + 1,5 периода замеров + отклик камеры
    assert 0.01 < leads[0.2] < leads[0.35] < leads[0.7] <= framing.LEAD_ZONE_SHARE * ZONE + 1e-6, leads
    assert leads[0.35] >= 0.055, leads  # спокойный шаг: запас — половина пути, который камера проедет вслепую (было 0,03)


def test_camera_stops_near_mark_when_walker_stops():
    """Человек шёл 0,35 кадра/с и встал: камера проезжает мимо него не больше чем на 0,09 кадра и успокаивается за 1,2 с.

    План ждал 0,06: не вышло. Проскок ≈ v·(заметить остановку 0,45 с + команда и камера 0,35 с) − путь торможения
    человека − запас. С малым запасом (LEAD 0,25) было 0,095; запас «как на макете» (0,55) оператор выбрал 2026-11-11 —
    ценой того, что на ходу человек дальше от точки (0,125 вместо 0,09)."""
    trs = trials(walk(0.35, t0=START, t1=STOP), STOP + 6)
    assert avg(overshoot_after(tr, STOP) for tr in trs) <= 0.09
    assert avg(settle_time(tr, STOP) for tr in trs) <= 1.2


def test_start_from_rest_on_calm_step_stays_at_zone_boundary():
    """Пошёл с места 0,35 кадра/с: выходит за границу обычной зоны не глубже 0,05 (было 0,10).

    Быстрый шаг 0,7 — не глубже 0,28: пока замер дойдёт и камера ответит (0,6–0,8 с), человек уже у края кадра;
    это предел стенда, а не закона (план ждал 0,06)."""
    for tr in trials(walk(0.35, t0=START, t1=STOP), STOP):
        assert zone_exit_depth(tr) <= 0.05
    for tr in trials(walk(0.7, t0=START, t1=START + 4), START + 5):
        assert zone_exit_depth(tr) <= 0.28


def test_camera_accelerates_harder_only_when_boundary_is_close():
    """Времени до границы мало — разгон сильнее характера. В свободной зоне граница дальше: срочный разгон
    начинается позже, когда человек ушёл от точки заметно дальше."""
    fn = walk(0.35, t0=START, t1=STOP)
    a_char = 1.0  # a_max «Обычно»

    def first_urgent(**kw):
        tr, rows = watched(fn, START + 3, **kw)
        t = next(r[0] for r in rows if r[3] > 1.5 * a_char)
        return t, tr.img_x[round(t / 0.04)] - 0.5

    (t_normal, x_normal), (t_loose, x_loose) = first_urgent(), first_urgent(zones={"waist_center": [0.5, 0.25, 1.8]})
    assert t_normal - START <= 0.75 and x_normal <= 0.15, (t_normal, x_normal)
    assert t_loose >= t_normal + 0.3 and x_loose >= x_normal + 0.1, (t_loose, x_loose)


def test_swayer_inside_zone_leaves_camera_still():
    """Переминается на ±0,08 кадра — внутри зоны: камера почти всё время стоит."""
    assert avg(moving_share(tr, 4.0) for tr in trials(stand(0.08), 30)) <= 0.1


def test_walking_speed_for_camera_is_smoothed_but_drops_at_once_on_stop():
    """Скорость идущего для камеры: подъёмы сглажены (качание шага ±0,1–0,17 кадра/с не разгоняет камеру), а упала
    вдвое — человек встал: камера получает это сразу. Средняя поэтому на 10–20 % ниже скорости человека."""
    for seed in SEEDS:
        rows = []
        run(walk(0.35, t0=START, t1=STOP), T=STOP + 1, seed=seed, **BENCH, **MJPEG1,
            controller_hook=lambda t, c, out, rows=rows: rows.append((t, c.x.person.walk * c.x.v_img, c.x.v_follow)))
        cruise = [r for r in rows if START + 3 <= r[0] <= STOP - 1]
        assert 0.26 <= avg(r[2] for r in cruise) <= 0.36, seed
        rises = [b[2] - a[2] for a, b in zip(cruise, cruise[1:])]
        # подъём — плавно: за такт 0,04 с не больше 0,12 кадра/с (замер 0,10; без сглаживания 0,19)
        assert max(rises) <= 0.12, (seed, max(rises))
        # остановка: оценка упала ниже половины — в тот же такт упала и скорость для камеры
        i = next(i for i, r in enumerate(rows) if r[0] > STOP - 0.5 and abs(r[1]) < 0.15)
        assert abs(rows[i][2]) <= 0.2, (seed, rows[i])
        assert abs(rows[i + 1][2]) <= 0.15, (seed, rows[i + 1])


def test_stop_is_one_command_once_camera_has_passed_the_person():
    """Человек резко встал, камера его проехала: регулятор решает «стоп» сразу, без ряда убывающих скоростей
    (каждая ждала бы своего окна 0,13 с — стенд 2026-11-11: стоп доходил до камеры через 0,5 с)."""
    t_stop = 8.0
    for seed in SEEDS:
        tr = run(snap_walk(0.31), T=t_stop + 2, seed=seed, **BENCH)
        i = next(i for i, (t, c) in enumerate(zip(tr.t, tr.codes)) if t > t_stop and c[0] == 50)
        assert tr.t[i] - t_stop <= 0.45, (seed, tr.t[i])
        assert all(c[0] <= 50 for c in tr.codes[i : i + 8]), (seed, [c[0] for c in tr.codes[i : i + 8]])
        j = next(j for j in range(i, len(tr.t)) if tr.applied[j][0] == 50)
        assert tr.t[j] - tr.t[i] <= 0.2, (seed, tr.t[j] - tr.t[i])


def test_return_after_overshoot_is_gentle_while_person_is_inside_zone():
    """После проскока человек стоит в зоне: камера подводит его к точке не быстрее RETURN_V — доводка, а не отскок."""
    for seed in SEEDS:
        tr = run(snap_walk(0.31), T=14.0, seed=seed, **BENCH)
        back = [abs(p) for t, p, x in zip(tr.t, tr.pan, tr.img_x)
                if t > 8.6 and p < 0 and abs(x - 0.5) < framing.RETURN_ZONE_FROM * ZONE]
        assert back and max(back) <= 1.15 * 0.12, (seed, max(back))


# (камера быстрее модели в столько раз, ошибка задержки видео в регуляторе) → обгон в среднем по настроечным маршрутам,
# замер 2026-11-11 + 15 %. Цель 0,22 при неверной модели не достигнута (подстройка скорости находит поправку за ~20 с);
# пороги — предохранитель от ухудшения, каждый для своего случая.
WRONG_MODEL = {(0.5, -0.1): 0.37, (0.5, 0.1): 0.27, (2.0, -0.1): 0.53, (2.0, 0.1): 0.34}


@pytest.mark.parametrize("speed_factor,latency_error", WRONG_MODEL)
def test_no_swinging_when_camera_model_is_wrong(speed_factor, latency_error):
    """Камера вдвое быстрее или медленнее модели, а задержка видео в регуляторе неверна на 0,1 с: сторож молчит,
    человек у края кадра не больше 5 % времени, обгон — не хуже замеренного."""
    over, edge = [], []
    for seed in ROUTES["настроечные"]:
        route = pm.RandomRoute(seed)

        def x(t, r=route):
            return 0.5 + r.x(t) / FRAME_M

        tr = run(x, seed=seed % 1000, speed_factor=speed_factor, ctrl_latency=0.15 + latency_error, **MJPEG1,
                 **EYE_BENCH)
        assert "diverged" not in tr.state, seed
        over.append(overtake_max(tr, x))
        edge.append(off_frame_share(tr))
    assert avg(over) <= WRONG_MODEL[speed_factor, latency_error], over
    assert avg(edge) <= 0.05, edge


def _peak_accel(tr, t0, t1, n=3):
    """Самое сильное ускорение камеры в окне, доли кадра/с² (по скорости настоящей камеры, шаг 0,12 с)."""
    v = [p for t, p in zip(tr.t, tr.cam_v) if t0 <= t <= t1]
    return max(abs(b - a) / (n * 0.04) for a, b in zip(v, v[n:]))


def test_reacquire_while_walking_is_not_harsher_than_a_start():
    """Человек пропал на ходу на 0,6 с и нашёлся: камера подхватывает его не резче, чем обычно трогается с места
    (ускорение камеры после находки — в среднем не больше полутора ускорений первого старта в том же прогоне;
    замер 1,0–1,5). План мерил рывок p99 всего прогона — он вдвое шумнее и растёт уже от того, что стартов два."""
    fn = walk(0.5, t0=START, t1=START + 6)
    ratios = []
    for seed in SEEDS:
        tr = run(fn, visible=lambda t: not (START + 3 <= t <= START + 3.6), T=START + 7, seed=seed, **BENCH, **MJPEG1)
        assert "diverged" not in tr.state
        ratios.append(_peak_accel(tr, START + 3.6, START + 5.1) / _peak_accel(tr, START, START + 1.5))
    assert avg(ratios) <= 1.5, ratios


# --- наклон: тот же закон, что у поворота (задача 4) ---


def diagonal(v, slope, t0=START, t1=STOP):
    """Проход наискось: (x(t), y(t)); slope — на сколько высоты кадра меняется высота головы за ширину кадра."""
    fx = walk(v, t0=t0, t1=t1)
    return fx, lambda t: 0.25 + slope * (fx(t) - 0.5)


@pytest.mark.parametrize("slope", [0.4, -0.4, 0.8])
def test_tilt_keeps_head_on_diagonal_walk(slope):
    """Человек идёт наискось 0,35 кадра/с: голова не уходит от точки по высоте дальше 0,2 высоты кадра
    (было до 0,5: наклон считал, что человек стоит, пока тот не уходил за кадр)."""
    fx, fy = diagonal(0.35, slope)
    for tr in trials(fx, STOP + 3, world_y=fy):
        assert "diverged" not in tr.state
        # крутой проход (0,28 высоты кадра в секунду): за время ответа камеры голова уходит на 0,21 — порог 0,23
        assert max(abs(y - 0.25) for t, y in zip(tr.t, tr.img_y) if t > START) <= (0.2 if abs(slope) <= 0.4 else 0.23)


def test_tilt_starts_with_pan_when_person_walks():
    """«Идёт» — свойство человека, а не оси: поворот увидел ходьбу — наклон ведёт голову сразу, не дожидаясь,
    пока уход по высоте сам станет похож на ходьбу."""
    fx, fy = diagonal(0.35, 0.4)
    tr = run(fx, T=START + 3, seed=0, world_y=fy, **BENCH, **MJPEG1)
    t_pan = next(t for t, p in zip(tr.t, tr.pan) if t > START and abs(p) > 0.05)
    t_tilt = next(t for t, p in zip(tr.t, tr.tilt) if t > START and abs(p) > 0.02)
    assert t_tilt - t_pan <= 0.3, (t_pan, t_tilt)


@pytest.mark.parametrize("zoom_factor", [0.7, 1.5])
def test_tilt_does_not_swing_while_zooming(zoom_factor):
    """Наезд на ходу (размер 0,06 → 0,19) при таблице зума, неверной в 0,7 и 1,5 раза: наклон не раскачивается —
    сторож молчит, голова не дальше 0,2 от точки (замер 0,09–0,10), заметных разворотов наклона (быстрее 0,1 высоты
    кадра/с) — не больше двух за 14 с.

    На малой скорости команда меняет знак чаще (до 8 раз при пороге 0,02 и до 4 при 0,05): доворот за зумом тянет
    вверх, ведение идущего — вниз, и на шаге 0,1 кадра/с они почти равны. Это ползание, а не качание: размах не растёт."""
    fx, fy = diagonal(0.1, 0.4, t0=2.0, t1=12.0)
    tr = run(fx, T=16.0, world_y=fy, world_u=lambda t: 0.06, zoom_speed_factor=zoom_factor, **BENCH, **MJPEG1)
    assert "diverged" not in tr.state
    assert max(abs(y - 0.25) for t, y in zip(tr.t, tr.img_y) if t > 2.0) <= 0.2
    assert reversals([v for t, v in zip(tr.t, tr.tilt) if t > 2.0], eps=0.1) <= 2


def test_tilt_keeps_moving_while_person_is_cut_by_frame_edge():
    """Быстрый проход наискось на крупном плане: человек наполовину за краем кадра, высоте головы не верим 0,8 с —
    но наклон не встаёт, а ещё секунду едет, как ехал (иначе голова уходит по высоте, пока поворот догоняет)."""
    fx, fy = diagonal(0.8, 0.6, t0=START, t1=START + 3)
    unsure = []
    tr = run(fx, seed=0, world_y=fy, controller_hook=lambda t, c, out: unsure.append(c._unsure), **MJPEG1,
             **dict(EYE_BENCH, T=START + 4))
    first = unsure.index(True)
    cut = [i for i in range(first, len(unsure)) if all(unsure[first : i + 1])]
    assert len(cut) * 0.04 >= 0.6, "замер высоты должен был стать ненадёжным надолго"
    late = [abs(tr.tilt[i]) for i in cut if 0.45 <= (i - first) * 0.04 <= 0.9]
    assert min(late) >= 0.2, late
