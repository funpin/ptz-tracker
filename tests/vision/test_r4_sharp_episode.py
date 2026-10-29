"""Цель после резкого движения камеры на записи живой AW-HE42 (площадка 2026-10-29, прогон 4 «Резко», первые 3 с).

Выбрана цель №2 (рамка 35×70 px, частично закрыта), рядом человек №1. Камера резко тронулась: за 0,5–0,9 с вся картинка
съехала на ~45 px влево и ~65 px вверх, детектор на смазанных кадрах цель не видел, а через 0,4 с тот же человек появился
как новый трек №3. Старый трекер так и остался «цель потеряна» (на площадке — на минуту, камера стояла).

Кадров в эпизоде нет: рамки людей берутся из записанных результатов анализа (то, что увидел детектор), а сдвиг картинки
считает та же модель камеры, что в программе, по записанным командам pan/tilt.
"""

from pathlib import Path

import numpy as np

from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.control.follower import sent_pt_codes
from ptztracker.control.framing import TILT_FROM_FH, FramingController
from ptztracker.recorder.reader import Episode
from ptztracker.recorder.replay import episode_calibration
from ptztracker.vision.tracker import PersonTracker
from ptztracker.vision.types import Person

EPISODE = Path(__file__).resolve().parents[1] / "data" / "episodes" / "2026-10-29-r4-sharp.zip"
ZOOM = 2047  # #GZ 7FF: зум в эпизоде не менялся


def _run(use_motion: bool):
    ep = Episode.open(EPISODE)
    calib, _ = episode_calibration(ep)
    ctl = FramingController(COMPOSITIONS["waist_center"], EffortProfile.at(0.5), calib, 0.12)
    tr = PersonTracker()
    motion = ctl.image_motion if use_motion else None
    log = []  # (t относительно первого анализа, id цели, состояние, рамка цели)
    t0 = None
    clicked = False
    for e in sorted(ep.events(), key=lambda e: e["t"]):
        if e["kind"] == "cmd":
            codes = sent_pt_codes(e)
            if codes is not None:
                ctl.x.dyn.command(e["t"], calib.pan_fw(codes[0], ZOOM))
                ctl.y.dyn.command(e["t"], calib.pan_fw(codes[1], ZOOM, "tilt") / TILT_FROM_FH)
        elif e["kind"] == "target" and e.get("how") == "click":
            clicked = e
        elif e["kind"] == "analysis":
            tc = e["t_capture"]
            t0 = tc if t0 is None else t0
            people = [
                Person(box=tuple(t["box"]), score=t["score"], keypoints=np.array(t["keypoints"], np.float32))
                for t in e["tracks"] if abs(t["last_seen"] - tc) < 1e-6
            ]
            tr.update(people, tc, None, frame_size=tuple(e["frame_size"]), motion=motion)
            if clicked:
                assert tr.select_at(clicked["x"], clicked["y"]) == 2
                clicked = False
            tgt = tr.target
            log.append((tc - t0, tr.target_id, tr.target_state, None if tgt is None else tgt.box))
    return tr, log


def test_episode_is_small():
    assert EPISODE.stat().st_size <= 1024 * 1024


def test_old_behaviour_loses_target_for_good():
    """Без поправки на движение камеры: после рывка цель потеряна и остаётся потерянной (то, что было на площадке)."""
    tr, log = _run(use_motion=False)
    assert log[-1][2] == "lost"


def test_target_survives_sharp_camera_move():
    tr, log = _run(use_motion=True)
    after = [row for row in log if row[0] > 1.0]
    assert after and all(row[2] == "tracking" for row in after[-10:]), log[-5:]
    assert all(row[1] == 2 for row in log if row[1] is not None)  # цель — всё тот же №2
    ad = tr.pop_adoptions()
    assert len(ad) <= 1 and all(a["how"] == "place" for a in ad)
    # цель — человек справа (бывший №2 / новый №3), а не человек №1 слева
    x_target = (log[-1][3][0] + log[-1][3][2]) / 2
    assert x_target > 320, log[-1]
