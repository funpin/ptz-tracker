"""Подстройка скорости на записи живой AW-HE42 (площадка 2026-10-29, прогон 1 «Пояс · центр», зум 555 → B46).

В записи поправка поворота дошла до предела 2,0: таблица без калибровки была одна на все зумы, а камера
держит постоянные °/с и в долях кадра на приближении едет в разы быстрее. Здесь подстройка повторяется
по тем же событиям, что видел Follower (отправленные #PTS/#Z — события `cmd`, ответы #GZ/#APC — такты
`control`), но с нынешней таблицей без калибровки. Кадров в эпизоде нет (обрезан): они тут не нужны.
"""

from pathlib import Path

from ptztracker.camera.session import UNCALIBRATED_SAFETY
from ptztracker.control.camera_model import SCALE_MAX, Calibration, default_calibration
from ptztracker.control.follower import sent_pt_codes, sent_zoom_code
from ptztracker.control.speed_adapt import SpeedAdapter
from ptztracker.recorder.reader import Episode

EPISODE = Path(__file__).resolve().parents[1] / "data" / "episodes" / "2026-10-29-r1-waist.zip"


def replay_adapt(ep: Episode, calib: Calibration) -> list[tuple[float, dict]]:
    """Подстройка по событиям записи, как в Follower: (момент, поправка) после каждого такта слежения."""
    adapter = SpeedAdapter(calib)
    following, zoom = False, 0x555
    gz_t = apc_t = None
    out = []
    for e in ep.events():
        kind = e.get("kind")
        if kind == "follow":
            following = True
            adapter.reset()
        elif kind == "cmd" and following:
            pt, zc = sent_pt_codes(e), sent_zoom_code(e)
            if pt is not None:
                adapter.pt_sent(e["t"], pt[0], pt[1], zoom)
            elif zc is not None:
                adapter.zoom_sent(e["t"], zc)
        elif kind == "control":
            frozen = e["out"]["state"] == "diverged"
            zoom = e["zoom_pos"]
            if e.get("zoom_pos_t") is not None and e["zoom_pos_t"] != gz_t:
                gz_t = e["zoom_pos_t"]
                adapter.zoom_measured(gz_t, zoom, frozen)
            if e.get("pan_tilt") is not None and e.get("pan_tilt_t") is not None and e["pan_tilt_t"] != apc_t:
                apc_t = e["pan_tilt_t"]
                adapter.pan_tilt_measured(apc_t, *e["pan_tilt"], zoom, frozen)
            out.append((e["t"], calib.speed_scale))
    return out


def test_episode_is_small_and_has_what_replay_needs():
    assert EPISODE.stat().st_size <= 1024 * 1024
    ep = Episode.open(EPISODE)
    assert ep.meta["camera"]["ip"] == "10.0.0.12" and not ep.meta["calibrated"]
    zooms = [e["zoom_pos"] for e in ep.events("control")]
    assert min(zooms) <= 0x560 and max(zooms) >= 0xB00  # зум прошёл 555 → B46
    assert sum(1 for e in ep.events("cmd") if sent_pt_codes(e)) > 100


def test_old_table_reproduces_scale_at_limit():
    """Таблица из записи (одна точка зума, ×1,5): повтор даёт ту же поправку, что была на площадке, — 2,0."""
    ep = Episode.open(EPISODE)
    trace = replay_adapt(ep, Calibration.from_json(ep.meta["calibration"]))
    recorded = [e["speed_scale"]["pan"] for e in ep.events("control")]
    assert recorded[-1] > 0.98 * SCALE_MAX
    assert trace[-1][1]["pan"] > 0.95 * SCALE_MAX


def test_deg_table_keeps_scale_away_from_limit():
    """Таблица в °/с (скорость в долях кадра растёт с зумом, как у камеры): поправка поворота держится около
    правды «камера / таблица» и не идёт к пределу 2,0; наклон берёт поправку поворота."""
    ep = Episode.open(EPISODE)
    calib = default_calibration(safety=UNCALIBRATED_SAFETY)
    trace = replay_adapt(ep, calib)
    pans = [s["pan"] for _, s in trace]
    assert max(pans) < 1.2, max(pans)  # было 2,0 (предел)
    # без запаса таблица — сама камера: поправка ≈ 1 / запас (замер: камера / таблица 0,97, без запаса 0,89…1,01)
    assert abs(pans[-1] * UNCALIBRATED_SAFETY - 1) < 0.1, pans[-1]
    assert trace[-1][1]["tilt"] == trace[-1][1]["pan"]  # наклон своей поправки не набрал
