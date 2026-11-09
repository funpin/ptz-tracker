"""Подстройка скорости во время слежения (control.speed_adapt): таблица «код → скорость» по #APC и #GZ."""

import math

from loop import QUEUE_MAX, run, sign_changes

from ptztracker.camera import he42
from ptztracker.camera import protocol as P
from ptztracker.control.camera_model import SCALE_MAX, default_calibration
from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.framing import WATCHDOG_MOVING_V
from ptztracker.control.speed_adapt import ADAPT_MIN_APC_UNITS, SpeedAdapter

AX = COMPOSITIONS["waist_center"].anchor[0]
WALK_V, LEG_S, STAND_S = 0.2, 5.0, 5.0


def _walk(t):
    """Ходит туда-сюда: LEG_S идёт со скоростью WALK_V (fw/s), STAND_S стоит."""
    ph = t % (2 * (LEG_S + STAND_S))
    x = WALK_V * min(ph, LEG_S)
    if ph > LEG_S + STAND_S:
        x -= WALK_V * min(ph - LEG_S - STAND_S, LEG_S)
    return AX + x


def _stands(t):
    """Человек стоит (с 1 с после остановки — без перелёта)."""
    ph = t % (LEG_S + STAND_S)
    return ph >= LEG_S + 1.0


def _at(tr, t):
    return tr.scale[round(t / 0.04)]


def _stand_reversals(tr):
    """Развороты камеры (команда за WATCHDOG_MOVING_V — камера правда едет) за каждое стояние."""
    out = []
    for k in range(int(tr.t[-1] // (LEG_S + STAND_S)) + 1):
        t0 = k * (LEG_S + STAND_S) + LEG_S
        out.append(sign_changes([p for t, p in zip(tr.t, tr.pan) if t0 <= t < t0 + STAND_S], eps=WATCHDOG_MOVING_V))
    return out


def test_camera_faster_than_table_adapts_while_walking():
    """Камера в 1,6 раза быстрее таблицы (и в 0,6 — медленнее): за 30 с ходьбы поправка подходит к правде
    ±0,15, раскачки нет: сторож молчит, за стояние — не больше одного разворота камеры, и за первое, пока
    поправка не дошла (Review Focus 2; до части 1, задачи 4 — двух). Без подстройки (коды по таблице) при ×1,6
    — до 4 разворотов."""
    for sf in (1.6, 0.6):
        tr = run(_walk, T=32, speed_factor=sf, cmd_delay=0.13, gz_period=0.5, apc_period=0.5, adapt=True)
        assert abs(_at(tr, 30.0)[0] - sf) <= 0.15, (sf, _at(tr, 30.0))
        assert "diverged" not in tr.state, sf
        rev = _stand_reversals(tr)
        assert all(n <= 1 for n in rev), (sf, rev)
        assert _at(tr, 30.0)[1] == _at(tr, 30.0)[0]  # наклон не ездил — берёт поправку поворота
        assert abs(_at(tr, 30.0)[2] - 1.0) < 0.02  # таблица зума верна — зум (отъезд у края) почти без поправки


def test_adaptation_prevents_swing_when_camera_faster():
    """Камера в 1,5 раза быстрее таблицы: поправка успевает раньше, раскачки нет. ×1,7…×2 — стабильно или стоп (test_framing)."""
    tr = run(_walk, T=40, speed_factor=1.5, gz_period=0.75, apc_period=0.75, adapt=True, channel=True)
    assert "diverged" not in tr.state
    assert abs(_at(tr, 30.0)[0] - 1.5) <= 0.15
    assert all(n <= 1 for n in _stand_reversals(tr))
    # без подстройки при ×3 раскачку ловит сторож: камера остановится и попросит калибровку
    assert "diverged" in run(_walk, T=40, speed_factor=3.0, cmd_delay=0.13, apc_period=0.5).state


def test_scale_unchanged_without_motion():
    """Человек стоит (и покачивается у кафедры внутри мёртвой зоны): камера не едет — поправка та же."""
    for x in (lambda t: AX, lambda t: AX + 0.03 * math.sin(2 * math.pi * t / 2.5)):
        tr = run(x, T=20, speed_factor=1.6, cmd_delay=0.13, gz_period=0.5, apc_period=0.5, adapt=True)
        assert all(s == (1.0, 1.0, 1.0) for s in tr.scale)


def test_adaptation_robust_to_dynamics_error():
    """Разгон камеры не тот, что в калибровке (τ ±0,1 с, мёртвое время 0…0,2 с при 0,08 в модели): при
    верной таблице поправка остаётся у 1 ±10% (замер: −6…+8%): отрезки берутся с одинаковой скоростью
    на концах, где ошибка разгона почти не сказывается. Это меньше ошибки таблицы, которую поправка исправляет."""
    for tau, dead in ((0.05, 0.08), (0.22, 0.08), (0.12, 0.0), (0.12, 0.2)):
        tr = run(_walk, T=32, channel=True, gz_period=0.75, apc_period=0.75, adapt=True, cam_tau=tau, cam_dead_time=dead)
        assert all(abs(s[0] - 1.0) <= 0.1 for s in tr.scale), (tau, dead, min(tr.scale), max(tr.scale))
        assert "diverged" not in tr.state, (tau, dead)


def _size_steps(t):
    """Человек стоит, а размер в кадре меняется ступенями (подошёл/отошёл): наезд, отъезд, наезд…"""
    return (0.08, 0.3)[int(t // 8) % 2]


def _zoom_model_error_moving(tr, t_from):
    """Наибольшее расхождение масштаба модели с камерой, пока зум едет (после t_from)."""
    lag = 12  # 0,48 с
    errs = [abs(math.exp(tr.model_zoom[i] - tr.cam_zoom[i]) - 1) for i in range(lag, len(tr.t))
            if tr.t[i] >= t_from and abs(tr.cam_zoom[i] - tr.cam_zoom[i - lag]) > 0.02]
    assert errs, "зум не ездил — проверять нечего"
    return max(errs)


# 0,06 кадра/с на 555 (3,7°/с) — на 0xAAA уже ~0,26 кадра/с; ходьба 8 с, как в тестах Review Focus 1
ZW_V, ZW_LEG, ZW_STAND = 0.06, 8.0, 8.0
ZW_APPROACH = 3 * (ZW_LEG + ZW_STAND)  # с этого момента подходит к камере (размер ×4) — отъезд на ходу


def _zoom_walk(t):
    """Ходит туда-сюда с постоянной скоростью в градусах (ZW_V в долях кадра на 555)."""
    ph = t % (2 * (ZW_LEG + ZW_STAND))
    x = ZW_V * min(ph, ZW_LEG)
    if ph > ZW_LEG + ZW_STAND:
        x -= ZW_V * min(ph - ZW_LEG - ZW_STAND, ZW_LEG)
    return AX + x


def _zoom_walk_u(t):
    """Мелкий на общем плане: на стояниях камера наезжает 555 → AAA; потом подходит — отъезд к 555 на ходу."""
    return 0.045 * (1 + 3 * min(1.0, max(0.0, t - ZW_APPROACH) / ZW_LEG))


def test_zoom_555_aaa_555_while_walking_no_swing_with_live_he42_speeds():
    """Площадка 2026-10-29: камера держит постоянные °/с, и в долях кадра на 0xAAA она в ~4 раза быстрее, чем
    на 555. Камера стенда — как живая HE42 (°/с по he42.PAN_DEG_S), таблица — без калибровки. Наезд 555 → AAA
    на стояниях, отъезд AAA → 555 на ходу: сторож молчит, за стояние не больше одного разворота поворота (Review Focus 2), за ходьбу наклон
    меняет знак не больше раза (на наезде 555 → A9D наклон дважды слабо меняет знак, ±0,02 — это наклон и зум
    мешают друг другу, часть 2, задача 3), человек не ближе 0,15 к краю; поправка остаётся у 1 (таблица — правда).
    На ходьбе у 0xAAA (≈0,27 кадра/с в кадре — обычная ходьба Review Focus 1 части 1) средняя ошибка
    без первой 1 с ≤ 0,05 кадра. Было (таблица одна на все зумы): наклон качался, на ~0x9C0 сторож
    выключал слежение."""
    from ptztracker.control.framing import WATCHDOG_MOVING_V

    T = ZW_APPROACH + 2 * (ZW_LEG + ZW_STAND)
    tr = run(_zoom_walk, world_u=_zoom_walk_u, T=T, zoom_pos=P.LENS_MIN, channel=True, gz_period=0.75,
             apc_period=0.75, adapt=True, cam_he42=True)
    assert "diverged" not in tr.state
    gz = [P.LENS_MIN + z / math.log(he42.ZOOM_RATIO) * (P.LENS_MAX - P.LENS_MIN) for z in tr.cam_zoom]
    assert max(gz) >= 0xA50 and gz[-1] <= 0x5A0, (hex(round(max(gz))), hex(round(gz[-1])))  # 555 → AAA → 555
    assert min(min(x, 1 - x) for x in tr.img_x) >= 0.09  # первая ходьба на широком угле, наезд ещё идёт
    assert all(abs(s[0] - 1.0) <= 0.1 and s[1] == s[0] for s in tr.scale), (min(tr.scale), max(tr.scale))
    cyc = ZW_LEG + ZW_STAND
    near_aaa = 0
    for k in range(int(T // cyc)):
        ws = k * cyc
        stand = [i for i, t in enumerate(tr.t) if ws + ZW_LEG <= t < ws + cyc]
        assert sign_changes([tr.pan[i] for i in stand], eps=WATCHDOG_MOVING_V) <= 1, ws
        walk = [i for i, t in enumerate(tr.t) if ws <= t < ws + ZW_LEG]
        assert sign_changes([tr.tilt[i] for i in walk], eps=WATCHDOG_MOVING_V * 9 / 16) <= 1, ws
        if gz[walk[0]] >= 0xA00:
            near_aaa += 1
            late = [abs(tr.img_x[i] - AX) for i in walk if tr.t[i] >= ws + 1.0]
            assert sum(late) / len(late) <= 0.1, (ws, sum(late) / len(late))
    assert near_aaa >= 1


def test_no_adaptation_needed_on_tele_with_live_he40_slowdown():
    """Выше 0xD54 камера замедляет поворот (he42.TELE_PAN_DEG_S), и таблица регулятора это знает: на ходьбе
    у 0xE00…0xFF0 подстройка скорости остаётся у 1 (±0,1) и сторож молчит."""

    def walk(t):  # как _walk, но быстрее: 0,4 fw/s — коды 60–65 на теле
        ph = t % (2 * (LEG_S + STAND_S))
        x = 0.4 * min(ph, LEG_S)
        if ph > LEG_S + STAND_S:
            x -= 0.4 * min(ph - LEG_S - STAND_S, LEG_S)
        return AX + x

    for zoom_pos in (0xE00, 0xF00, 0xFF0):
        tr = run(walk, world_u=lambda t: 0.19, T=40, zoom_pos=zoom_pos, cmd_delay=QUEUE_MAX, gz_period=0.5,
                 apc_period=0.5, adapt=True, cam_he42=True)
        assert "diverged" not in tr.state, hex(zoom_pos)
        assert all(abs(s[0] - 1.0) <= 0.1 and abs(s[1] - 1.0) <= 0.1 for s in tr.scale), (hex(zoom_pos), tr.scale[-1])


def test_tilt_quiet_while_standing_during_zoom_in_with_live_he42_speeds():
    """Площадка 2026-10-29, прогон 4б: после наезда зума до ~0xA8E наклон закачался (коды 23↔75, ошибка до ±0,58,
    сторож выключил слежение). Человек стоит, мелкий на общем плане, камера наезжает 555 → ~AA0 (сдвиг якоря
    от зума доворачивается вместе с ним), камера — как живая HE42 (°/с), и ещё в 1,5–2 раза быстрее по вертикали:
    ошибка по y не больше 0,09 кадра, за любую секунду не больше одного разворота наклона (за команду
    сильнее WATCHDOG_MOVING_V·9/16; при камере ×2 и очереди 0,13 — не больше двух, крайний случай из DECISIONS), после наезда наклон молчит, сторож не срабатывает. Дрожание носа по y
    на записи 0,005–0,01 кадра (σ) — раскачку не даёт, в тесте его нет."""
    from ptztracker.control.framing import WATCHDOG_MOVING_V

    eps = WATCHDOG_MOVING_V * 9 / 16
    ay = COMPOSITIONS["waist_center"].anchor[1]
    for wy, sf in ((0.25, 1.0), (0.35, 1.0), (0.25, 1.5), (0.25, 2.0)):
        tr = run(lambda t: AX, world_y=lambda t, wy=wy: wy, world_u=lambda t: 0.045, T=45, zoom_pos=P.LENS_MIN,
                 channel=True, gz_period=0.75, apc_period=0.75, adapt=True, cam_he42=True, speed_factor=sf)
        gz = [P.LENS_MIN + z / math.log(he42.ZOOM_RATIO) * (P.LENS_MAX - P.LENS_MIN) for z in tr.cam_zoom]
        key = (wy, sf)
        assert "diverged" not in tr.state, key
        assert max(gz) >= 0xA00, (key, hex(round(max(gz))))
        assert max(abs(y - ay) for y in tr.img_y[25:]) <= (0.16 if sf >= 2.0 else 0.11), key
        for k in range(45):
            win = [tr.tilt[i] for i, t in enumerate(tr.t) if k <= t < k + 1]
            # камера ×2 по вертикали при очереди 0,13 с — названный предел (как при неверной таблице ×2): 2 разворота за секунду
            assert sign_changes(win, eps=eps) <= (2 if sf >= 2.0 else 1), (key, k)
        assert max(abs(x) for x in tr.tilt[round(30 / 0.04):]) <= eps, key  # наезд кончился — наклон молчит


def test_zoom_table_wrong_model_within_10pct_after_adaptation():
    """Таблица зума врёт в 1,5 раза (и в 0,7): после двух наездов/отъездов модель зума совпадает с #GZ
    в пределах 10% и во время движения зума (часть 1, Review Focus 3; без подстройки — до 20%)."""
    for zsf in (1.5, 0.7):
        common = dict(world_u=_size_steps, T=48, zoom_speed_factor=zsf, cmd_delay=0.065, gz_period=0.5, apc_period=0.5)
        tr = run(lambda t: AX, adapt=True, **common)
        assert abs(tr.scale[-1][2] - zsf) <= 0.05 * zsf, (zsf, tr.scale[-1])
        assert _zoom_model_error_moving(tr, 16.0) < 0.10, zsf
        assert _zoom_model_error_moving(run(lambda t: AX, **common), 16.0) > 0.10, zsf  # тест что-то проверяет


# --- SpeedAdapter напрямую ------------------------------------------------------------------------

Z = 0x73F
UNITS_PER_FW = he42.hfov_deg((Z - P.LENS_MIN) / (P.LENS_MAX - P.LENS_MIN)) / he42.APC_DEG_PER_UNIT


def _drive(adapter, true_factor, T=20.0, code=70, frozen=lambda t: False, apc_noise=lambda i: 0, zoom=lambda t: Z,
           t_cmd=0.0, seen=None):
    """Камера едет ровно кодом code (pan и tilt, команда в t_cmd) в true_factor раз быстрее таблицы; #APC раз в 0,5 с."""
    calib = adapter.calib
    v = calib.pan.speed(code, Z) * true_factor
    from ptztracker.control.camera_model import CameraDynamics

    cam = CameraDynamics(calib.dead_time, calib.tau)
    cam.command(t_cmd, v)
    adapter.pt_sent(t_cmd, code, code, Z)
    for i in range(1, int(T / 0.5) + 1):
        t = 0.5 * i
        pos = round(0x8000 + cam.position(t) * UNITS_PER_FW) + apc_noise(i)
        adapter.pan_tilt_measured(t, pos, pos, zoom(t), frozen=frozen(t))
        if seen is not None:
            seen.append(calib.pan_scale)


def test_adapter_converges_and_clamps():
    calib = default_calibration()
    _drive(SpeedAdapter(calib), 1.6)
    assert abs(calib.pan_scale - 1.6) < 0.05 and abs(calib.tilt_scale - 1.6) < 0.05
    calib = default_calibration()
    _drive(SpeedAdapter(calib), 3.0)
    assert 1.95 < calib.pan_scale <= SCALE_MAX  # дальше — не поправка, а другая камера


def _drive_axes(adapter, pan_factor, tilt_factor, T=20.0, pan_code=70, tilt_code=50, tilt_true=1.0):
    """Поворот и наклон своими кодами, каждая ось — в свой раз быстрее таблицы; #APC раз в 0,5 с. tilt_true —
    наклон камеры ÷ поворот на одном коде (замер мастера, если он есть, — уже в таблице калибровки)."""
    from ptztracker.control.camera_model import CameraDynamics

    calib = adapter.calib
    cams = []
    for code, f in ((pan_code, pan_factor), (tilt_code, tilt_factor * tilt_true)):
        cam = CameraDynamics(calib.dead_time, calib.tau)
        cam.command(0.0, calib.pan.speed(code, Z) * f)
        cams.append(cam)
    adapter.pt_sent(0.0, pan_code, tilt_code, Z)
    for i in range(1, int(T / 0.5) + 1):
        t = 0.5 * i
        p, q = (round(0x8000 + c.position(t) * UNITS_PER_FW) for c in cams)
        adapter.pan_tilt_measured(t, p, q, Z)


def test_tilt_takes_pan_scale_until_it_has_its_own():
    """Площадка 2026-10-29: камера в °/с одинакова по обеим осям, а наклон почти не ездит прямыми отрезками —
    своей поправки не набирает (была 1,0 при 2,0 у поворота, и наклон качался). Пока своей нет — берётся
    поправка поворота; когда наклон сам проехал годные отрезки — своя."""
    calib = default_calibration()
    _drive_axes(SpeedAdapter(calib), 1.6, 1.0)  # наклон стоит (код 50)
    assert abs(calib.pan_scale - 1.6) < 0.05 and calib.tilt_scale == calib.pan_scale
    calib = default_calibration()
    _drive_axes(SpeedAdapter(calib), 1.6, 0.8, tilt_code=70)  # наклон едет сам и в 0,8 раза
    assert abs(calib.pan_scale - 1.6) < 0.05 and abs(calib.tilt_scale - 0.8) < 0.05


def test_adapter_frozen_while_watchdog_tripped():
    calib = default_calibration()
    _drive(SpeedAdapter(calib), 1.6, frozen=lambda t: True)
    assert calib.speed_scale == {"pan": 1.0, "tilt": 1.0, "zoom": 1.0}


def test_adapter_ignores_one_outlier():
    """Один сбойный ответ #APC (скачок на 2000 единиц) почти не сдвигает поправку: медиана последних отношений."""
    calib = default_calibration()
    _drive(SpeedAdapter(calib), 1.0, T=10.0, apc_noise=lambda i: 2000 if i >= 12 else 0)
    assert abs(calib.pan_scale - 1.0) < 0.02


def test_adapter_skips_while_zoom_moves_and_small_moves():
    calib = default_calibration()
    _drive(SpeedAdapter(calib), 1.6, zoom=lambda t: Z + round(100 * t))  # зум едет: скорость pan в fw другая
    assert calib.pan_scale == 1.0
    calib = default_calibration()
    # медленный код: за 0,5 с меньше ADAPT_MIN_APC_UNITS — шум положения, не замер
    assert calib.pan.speed(55, Z) * 1.6 * 0.5 * UNITS_PER_FW < ADAPT_MIN_APC_UNITS
    _drive(SpeedAdapter(calib), 1.6, code=55)
    assert calib.pan_scale == 1.0


def test_calibration_codes_use_scale():
    """Камера в 1,6 раза быстрее таблицы: код — на скорость v/1,6; скорость кода и предел — ×1,6."""
    calib = default_calibration()
    calib.pan_scale = 1.6
    assert calib.pan_code(0.4, Z) == calib.pan.code_for(0.4 / 1.6, Z)
    assert calib.pan_fw(70, Z) == calib.pan.speed(70, Z) * 1.6
    assert calib.pan_max_fw(Z) == calib.pan.speed(99, Z) * 1.6
    assert calib.pan_code(0.4, Z, axis="tilt") == calib.pan.code_for(0.4, Z)  # у наклона своя поправка
    calib.zoom_scale = 0.5
    assert calib.zoom_rate(75) == calib.zoom.rate(75) * 0.5
    assert calib.zoom_code(0.1) == calib.zoom.code_for(0.2)


def test_calibration_json_has_no_scale_and_validates():
    """Поправка хранится только в CameraConfig.speed_scale: в JSON калибровки её нет, а старый JSON с ней
    читается (ключ пропускается). set_speed_scale пропускает мусор и ограничивает пределами."""
    from ptztracker.control.camera_model import Calibration

    calib = default_calibration()
    calib.pan_scale, calib.zoom_scale = 1.3, 0.8
    d = calib.to_json()
    assert "speed_scale" not in d
    assert Calibration.from_json({**d, "speed_scale": {"pan": 1.7}}).speed_scale == {"pan": 1.0, "tilt": 1.0, "zoom": 1.0}
    bad = default_calibration()
    bad.set_speed_scale({"pan": "x", "tilt": 9.0, "zoom": -1})
    assert bad.speed_scale == {"pan": 1.0, "tilt": SCALE_MAX, "zoom": 1.0}
    bad.set_speed_scale([1, 2])  # не словарь — ничего не меняет
    assert bad.speed_scale == {"pan": 1.0, "tilt": SCALE_MAX, "zoom": 1.0}


def test_adapter_ignores_early_glitch():
    """Сбой #APC (−300 единиц) на втором ответе, пока история отношений не набралась: поправка не уходит
    (без этого — 0,79). Первые ADAPT_MIN_RATIOS − 1 отношений только копятся."""
    calib = default_calibration()
    # камера уже едет ровно: первый же отрезок годен, и сбой — первое отношение в истории
    seen = []
    _drive(SpeedAdapter(calib), 1.0, T=10.0, apc_noise=lambda i: -300 if i == 2 else 0, t_cmd=-5.0, seen=seen)
    assert all(abs(v - 1.0) < 0.02 for v in seen), seen


def test_session_applies_saved_speed_scale():
    """Поправка из настроек камеры ложится на её калибровку (и на средние значения без калибровки — если она
    найдена для нынешней таблицы: с ярлыком DEFAULT_TABLE_ID). Поправка без ярлыка у камеры без калибровки
    найдена для прежней таблицы (одна точка по зуму; на площадке 2026-10-29 — 2,0) и не берётся."""
    from ptztracker.camera.session import CameraSession
    from ptztracker.config import CameraConfig
    from ptztracker.control.camera_model import DEFAULT_TABLE_ID

    def scale_of(speed_scale, calibrated):
        cfg = CameraConfig(id="x", name="X", ip="sim", kind="sim", speed_scale=speed_scale,
                           calibration=default_calibration().to_json() if calibrated else None)
        return CameraSession(cfg, analysis=False).calibration().speed_scale

    want = {"pan": 1.3, "tilt": 1.0, "zoom": SCALE_MAX}
    assert scale_of({"pan": 1.3, "zoom": 9.0}, True) == want  # у калибровки мастера ярлык не нужен
    assert scale_of({"pan": 1.3, "zoom": 9.0, "table": DEFAULT_TABLE_ID}, False) == want
    # без ярлыка pan/tilt отбрасываются (таблица поворота другая), зум от неё не зависит — остаётся
    assert scale_of({"pan": 2.0, "tilt": 1.5, "zoom": 0.8}, False) == {"pan": 1.0, "tilt": 1.0, "zoom": 0.8}


def test_measured_tilt_is_not_counted_twice_by_adapter():
    """Мастер измерил наклон ×0,7 от поворота, камера так и едет: поправка наклона остаётся ~1. Было: подстройка
    сравнивала с таблицей поворота, находила 0,7, а модель умножала её на замер — 0,49 (камера «вдвое медленнее»
    и регулятор гнал наклон)."""
    calib = default_calibration()
    calib.tilt = [(63, 0.7), (75, 0.7)]
    _drive_axes(SpeedAdapter(calib), 1.0, 1.0, tilt_code=70, tilt_true=0.7)
    assert abs(calib.tilt_scale - 1.0) < 0.05, calib.tilt_scale
    assert abs(calib.pan_fw(70, Z, axis="tilt") / (0.7 * calib.pan.speed(70, Z)) - 1) < 0.05


def _walk_y(t):
    """Ходит вверх-вниз (лестница, сцена): как _walk, по высоте кадра."""
    return 0.25 + (_walk(t) - AX)


def test_measured_tilt_loop_no_overdrive():
    """Виртуальный стенд: камера наклоняется ×0,7 от поворота, калибровка это знает. Поправка наклона у 1,
    на стояниях наклон не качается (не больше одного разворота)."""
    tr = run(lambda t: AX, world_y=_walk_y, T=60.0, adapt=True, apc_period=0.5, gz_period=0.7,
             tilt_factor=0.7, measured_tilt=True)
    assert abs(_at(tr, 59.9)[1] - 1.0) < 0.1, _at(tr, 59.9)
    for k in range(6):
        t0 = k * (LEG_S + STAND_S) + LEG_S + 1.0
        win = [y for t, y in zip(tr.t, tr.tilt) if t0 <= t < t0 + STAND_S - 1.0]
        assert sign_changes(win, eps=WATCHDOG_MOVING_V * 9 / 16) <= 1, k
