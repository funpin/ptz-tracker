"""Подстройка скорости во время слежения (control.speed_adapt): таблица «код → скорость» по #APC и #GZ."""

import math

from loop import run, sign_changes

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
    ±0,15, раскачки нет: сторож молчит, за стояние — не больше одного разворота камеры (за первое,
    пока поправка не дошла, — двух). Без подстройки (коды по таблице) при ×1,6 — до 4 разворотов."""
    for sf in (1.6, 0.6):
        tr = run(_walk, T=32, speed_factor=sf, cmd_delay=0.13, gz_period=0.5, apc_period=0.5, adapt=True)
        assert abs(_at(tr, 30.0)[0] - sf) <= 0.15, (sf, _at(tr, 30.0))
        assert "diverged" not in tr.state, sf
        rev = _stand_reversals(tr)
        assert rev[0] <= 2 and all(n <= 1 for n in rev[1:]), (sf, rev)
        assert _at(tr, 30.0)[1] == 1.0  # наклон не ездил — его не трогаем
        assert abs(_at(tr, 30.0)[2] - 1.0) < 0.02  # таблица зума верна — зум (отъезд у края) почти без поправки


def test_adaptation_prevents_swing_at_x2():
    """Камера вдвое быстрее таблицы (очередь 0,13 с): без подстройки сторож ловит раскачку; с ней поправка
    успевает раньше, раскачки нет."""
    tr = run(_walk, T=40, speed_factor=2.0, cmd_delay=0.13, gz_period=0.5, apc_period=0.5, adapt=True)
    assert "diverged" not in tr.state
    assert abs(_at(tr, 30.0)[0] - 2.0) <= 0.15
    assert all(n <= 1 for n in _stand_reversals(tr)[1:])
    assert "diverged" in run(_walk, T=40, speed_factor=2.0, cmd_delay=0.13, apc_period=0.5).state


def test_scale_unchanged_without_motion():
    """Человек стоит (и покачивается у кафедры внутри мёртвой зоны): камера не едет — поправка та же."""
    for x in (lambda t: AX, lambda t: AX + 0.03 * math.sin(2 * math.pi * t / 2.5)):
        tr = run(x, T=20, speed_factor=1.6, cmd_delay=0.13, gz_period=0.5, apc_period=0.5, adapt=True)
        assert all(s == (1.0, 1.0, 1.0) for s in tr.scale)


def test_adaptation_robust_to_dynamics_error():
    """Разгон камеры не тот, что в калибровке (τ ±0,1 с, мёртвое время 0…0,2 с при 0,08 в модели): при
    верной таблице поправка остаётся у 1 ±10% (замер: −6…+8%): отрезки берутся с одинаковой скоростью
    на концах, где ошибка разгона почти не сказывается. Это меньше ошибки таблицы, которую поправка исправляет."""
    for tau, dead in ((0.27, 0.08), (0.47, 0.08), (0.37, 0.0), (0.37, 0.2)):
        tr = run(_walk, T=32, cmd_delay=0.13, gz_period=0.5, apc_period=0.5, adapt=True, cam_tau=tau, cam_dead_time=dead)
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


def _drive(adapter, true_factor, T=20.0, code=70, frozen=lambda t: False, apc_noise=lambda i: 0, zoom=lambda t: Z):
    """Камера едет ровно кодом code (pan и tilt) в true_factor раз быстрее таблицы; #APC раз в 0,5 с."""
    calib = adapter.calib
    v = calib.pan.speed(code, Z) * true_factor
    from ptztracker.control.camera_model import CameraDynamics

    cam = CameraDynamics(calib.dead_time, calib.tau)
    cam.command(0.0, v)
    adapter.pt_sent(0.0, code, code, Z)
    for i in range(1, int(T / 0.5) + 1):
        t = 0.5 * i
        pos = round(0x8000 + cam.position(t) * UNITS_PER_FW) + apc_noise(i)
        adapter.pan_tilt_measured(t, pos, pos, zoom(t), frozen=frozen(t))


def test_adapter_converges_and_clamps():
    calib = default_calibration()
    _drive(SpeedAdapter(calib), 1.6)
    assert abs(calib.pan_scale - 1.6) < 0.05 and abs(calib.tilt_scale - 1.6) < 0.05
    calib = default_calibration()
    _drive(SpeedAdapter(calib), 3.0)
    assert 1.95 < calib.pan_scale <= SCALE_MAX  # дальше — не поправка, а другая камера


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
    assert calib.pan.speed(56, Z) * 1.6 * 0.5 * UNITS_PER_FW < ADAPT_MIN_APC_UNITS
    _drive(SpeedAdapter(calib), 1.6, code=56)
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


def test_calibration_json_keeps_scale():
    from ptztracker.control.camera_model import Calibration

    calib = default_calibration()
    calib.pan_scale, calib.zoom_scale = 1.3, 0.8
    back = Calibration.from_json(calib.to_json())
    assert back.speed_scale == {"pan": 1.3, "tilt": 1.0, "zoom": 0.8}
    old = calib.to_json()
    del old["speed_scale"]  # калибровка старой версии
    assert Calibration.from_json(old).speed_scale == {"pan": 1.0, "tilt": 1.0, "zoom": 1.0}
    bad = Calibration.from_json({**calib.to_json(), "speed_scale": {"pan": "x", "tilt": 9.0, "zoom": -1}})
    assert bad.speed_scale == {"pan": 1.0, "tilt": SCALE_MAX, "zoom": 1.0}


def test_session_applies_saved_speed_scale():
    """Поправка из настроек камеры ложится на её калибровку (и на средние значения без калибровки)."""
    from ptztracker.camera.session import CameraSession
    from ptztracker.config import CameraConfig

    for calibrated in (False, True):
        cfg = CameraConfig(id="x", name="X", ip="sim", kind="sim", speed_scale={"pan": 1.3, "zoom": 9.0},
                           calibration=default_calibration().to_json() if calibrated else None)
        calib = CameraSession(cfg, analysis=False).calibration()
        assert calib.speed_scale == {"pan": 1.3, "tilt": 1.0, "zoom": SCALE_MAX}, calibrated
