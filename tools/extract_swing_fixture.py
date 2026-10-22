"""Собрать tests/data/watchdog/part0_swings.json.gz из записей части 0 (сторож раскачки, часть 1, задача 2).

    .venv/bin/python tools/extract_swing_fixture.py ЗАПИСЬ.zip [ЗАПИСЬ.zip …]

Записи — 90-секундные прогоны слежения на симуляторе из docs/field/2026-10-22-part0-analysis.md
(«runs/<имя>_rec/*.zip»). Целиком они 5–8 МБ, поэтому в репозиторий идёт выжимка: из каждой записи с
«РАСКАЧКОЙ» — первое включение слежения до первого такта в состоянии `diverged`.

Формат (JSON-список, по элементу на запись; время — секунды от включения слежения):
- `run` — имя прогона (папка без `_rec`), `diverged_t` — когда сработал сторож,
  `dead_time`, `tau` — калибровка записи;
- `ticks` — такты: `[t, pan, tilt, err_x, err_y, soft_x, soft_y, target_x, target_y, dead_x, dead_y]`:
  записанные команды (fw/s, fh/s) и ошибка регулятора, мягкая и мёртвая зоны, где человек «в мире» в долях
  кадра при текущем зуме (`null`, пока замера нет) — так, как его видел регулятор той версии;
- `zoom` — ушедшие в камеру команды зума `[t, ln/с]`; `pos` — ответы #APC `[t, pan, tilt]`.

Положение «в мире» записи не хранят — оно восстанавливается кодом самого регулятора (`_Axis.measure`) по
записанным замерам, командам pan/tilt и модели зума той версии (по решённой команде зума `out.zoom`).
"""

import gzip
import json
import math
import sys
from pathlib import Path

from ptztracker.control.camera_model import CameraDynamics
from ptztracker.control.follower import sent_zoom_code
from ptztracker.control.framing import _Axis
from ptztracker.recorder.reader import Episode
from ptztracker.recorder.replay import episode_calibration

DST = Path(__file__).resolve().parents[1] / "tests" / "data" / "watchdog" / "part0_swings.json.gz"


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def extract(path: Path) -> dict | None:
    ep = Episode.open(path)
    calib, _ = episode_calibration(ep)
    evs = sorted((e for e in ep.events() if isinstance(e.get("t"), (int, float))), key=lambda e: e["t"])
    seg = None
    for e in evs:
        k = e.get("kind")
        if seg is None:
            if k == "follow" and e.get("on") is True:
                t_on, latency = e["t"], float(e.get("video_latency", calib.latency_for(ep.meta.get("stream_kind"))))
                zd = CameraDynamics(calib.dead_time, calib.tau)  # модель зума той версии: по решению
                ax = (_Axis(+1, CameraDynamics(calib.dead_time, calib.tau)),
                      _Axis(-1, CameraDynamics(calib.dead_time, calib.tau)))
                last_cap = None
                seg = dict(run=path.parent.name.removesuffix("_rec"), diverged_t=None, dead_time=calib.dead_time,
                           tau=calib.tau, ticks=[], zoom=[], pos=[])
            continue
        if k == "follow" and e.get("on") is False:
            return None  # выключилось не раскачкой
        if k == "cmd":
            c = sent_zoom_code(e)
            if c is not None:
                seg["zoom"].append([_r(e["t"] - t_on), _r(calib.zoom.rate(c), 5)])
        elif k == "position":
            seg["pos"].append([_r(e["t"] - t_on), int(e["pan"]), int(e["tilt"])])
        elif k == "control":
            t, o, ef, m = e["t"], e["out"], e["effort"], e.get("measure")
            if o["state"] == "diverged":
                seg["diverged_t"] = _r(t - t_on)
                return seg
            cap = e.get("t_capture")
            if m is not None and cap is not None and cap != last_cap:
                last_cap = cap
                t_shot = cap - latency
                k_shot = math.exp(zd.position(t_shot))
                ax[0].measure(m["nose"][0], t_shot, k_shot)
                ax[1].measure(m["nose"][1], t_shot, k_shot)
            k_now = math.exp(zd.position(t))
            tgt = [None if a.world is None else (a.world + a.v_world * (t - a.t_meas)) * k_now for a in ax]
            dead_y = ef["dead_y"] * float(e.get("aspect", 16 / 9)) / (16 / 9)
            seg["ticks"].append([_r(t - t_on), _r(o["pan"], 5), _r(o["tilt"], 5), _r(o["error"][0]), _r(o["error"][1]),
                                 ef["soft_x"], ef["soft_y"], _r(tgt[0]), _r(tgt[1]), ef["dead_x"], _r(dead_y)])
            # как регулятор той версии: модель камеры — по своим командам, модель зума — по решённой
            ax[0].dyn.command(t, o["pan"] / k_now)
            ax[1].dyn.command(t, o["tilt"] / k_now)
            zd.command(t, o["zoom"])
    return None


def main() -> int:
    segs = [s for s in (extract(Path(p)) for p in sorted(sys.argv[1:])) if s is not None]
    for s in segs:
        print(s["run"], s["diverged_t"], len(s["ticks"]))
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_bytes(gzip.compress(json.dumps(segs, separators=(",", ":")).encode(), mtime=0))
    print(f"{DST}: {DST.stat().st_size / 1e3:.0f} КБ, {len(segs)} записей")
    return 0


if __name__ == "__main__":
    sys.exit(main())
