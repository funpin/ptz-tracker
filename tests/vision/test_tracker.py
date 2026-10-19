import numpy as np

from ptztracker.vision.tracker import PersonTracker, appearance_distance, torso_histogram
from ptztracker.vision.types import Person

RED, BLUE, GREEN = (40, 40, 220), (220, 60, 40), (40, 200, 40)
DT = 0.066  # ~15 Гц


def scene(*people):
    """Кадр и список Person: people = (cx, top, color[, w, h, score, hips])."""
    img = np.full((720, 1280, 3), 25, np.uint8)
    out = []
    for spec in people:
        cx, top, color, *rest = spec
        w, h, score, hips = (rest + [120, 300, 0.9, True][len(rest):])[:4]
        x1, x2 = int(cx - w / 2), int(cx + w / 2)
        img[int(top + 0.15 * h) : int(top + 0.6 * h), x1:x2] = color
        kp = np.zeros((17, 3), np.float32)
        kp[0] = (cx, top + 0.08 * h, 0.9)
        kp[5] = (cx + w / 3, top + 0.2 * h, 0.9)
        kp[6] = (cx - w / 3, top + 0.2 * h, 0.9)
        if hips:
            kp[11] = (cx + w / 5, top + 0.55 * h, 0.9)
            kp[12] = (cx - w / 5, top + 0.55 * h, 0.9)
        out.append(Person(box=(cx - w / 2, top, cx + w / 2, top + h), score=score, keypoints=kp))
    return img, out


def step(tr, t, *people):
    img, ppl = scene(*people)
    return tr.update(ppl, t, img)


def test_ids_persist_while_moving():
    tr = PersonTracker()
    ids = {step(tr, i * DT, (300 + 8 * i, 200, RED))[0].id for i in range(30)}
    assert len(ids) == 1


def test_two_people_get_distinct_ids():
    tr = PersonTracker()
    seen = step(tr, 0.0, (300, 200, RED), (800, 200, BLUE))
    assert len({t.id for t in seen}) == 2


def test_select_at_smallest_containing_box_and_miss_keeps_target():
    tr = PersonTracker()
    seen = step(tr, 0.0, (400, 100, RED, 400, 600), (400, 250, BLUE, 100, 200))
    small = min(seen, key=lambda t: (t.box[2] - t.box[0]) * (t.box[3] - t.box[1]))
    assert tr.select_at(400, 300) == small.id
    assert tr.select_at(5, 5) is None
    assert tr.target_id == small.id and tr.target_state == "tracking"


def test_brief_occlusion_keeps_id():
    tr = PersonTracker()
    first = step(tr, 0.0, (500, 200, RED))[0]
    tr.select(first.id)
    t = 0.0
    for _ in range(22):  # ~1.5 с без детекции (кафедра закрыла)
        t += DT
        step(tr, t)
    assert tr.target_state == "lost"
    t += DT
    again = step(tr, t, (510, 200, RED))
    assert again[0].id == first.id and tr.target_state == "tracking"


def test_target_not_stolen_by_different_looking_neighbour():
    tr = PersonTracker()
    red = step(tr, 0.0, (500, 200, RED))[0]
    tr.select(red.id)
    blue = step(tr, DT, (520, 200, BLUE))
    assert tr.target_id == red.id and tr.target_state == "lost"
    assert blue[0].id != red.id


def test_target_reacquired_by_appearance_after_timeout():
    tr = PersonTracker()
    red = step(tr, 0.0, (300, 200, RED))[0]
    tr.select(red.id)
    t = 0.0
    for _ in range(46):  # ~3 с — трек удалён
        t += DT
        step(tr, t)
    assert all(x.id != red.id for x in tr.tracks)
    t += DT
    back = step(tr, t, (900, 200, RED), (300, 200, BLUE))
    new_red = next(x for x in back if x.box[0] > 600)
    assert tr.target_id == new_red.id and tr.target_state == "tracking"


def test_weak_detection_continues_but_does_not_create():
    tr = PersonTracker()
    a = step(tr, 0.0, (400, 200, RED))[0]
    cont = step(tr, DT, (405, 200, RED, 120, 300, 0.15))
    assert [x.id for x in cont] == [a.id]
    assert step(tr, 2 * DT, (405, 200, RED, 120, 300, 0.15), (1000, 200, BLUE, 120, 300, 0.15)) and len(tr.tracks) == 1


def test_torso_histogram_without_hips():
    img, ppl = scene((500, 100, RED, 300, 700, 0.9, False))  # крупно, бёдер нет
    h = torso_histogram(img, ppl[0])
    ref = np.full((720, 1280, 3), RED, np.uint8)
    whole = Person(box=(0, 0, 1280, 720), score=1.0, keypoints=np.zeros((17, 3), np.float32))
    assert h is not None and appearance_distance(h, torso_histogram(ref, whole)) < 0.2


def test_no_people_no_crash():
    tr = PersonTracker()
    img, _ = scene()
    assert tr.update([], 0.0, img) == [] and tr.target_state == "none"
