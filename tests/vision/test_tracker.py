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
    assert tr.target_id == red.id  # узнанная цель сохраняет свой номер (оператор видит «№3», а не «№7»)


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


def test_velocity_spike_does_not_lose_target_after_occlusion():
    # Final review I2: два кадра почти в одно время (пачка RTSP) + дрожание рамки → не улетать прогнозом
    tr = PersonTracker()
    a = step(tr, 0.0, (500, 200, RED))[0]
    tr.select(a.id)
    step(tr, 0.002, (508, 200, RED))  # 8 px за 2 мс
    t = 0.002
    for _ in range(15):  # ~1 с перекрытия
        t += DT
        step(tr, t)
    t += DT
    back = step(tr, t, (505, 200, RED, 120, 300, 0.20))  # слабая детекция на том же месте
    assert [x.id for x in back] == [a.id] and tr.target_state == "tracking"


def _partly_recolored(cx, top, color, frac, w=120, h=300):
    """Кадр с человеком, у которого часть «рубашки» другого цвета — шум сжатия/света."""
    img, ppl = scene((cx, top, RED), (cx - 190, top - 30, BLUE, 300, 340))
    x1 = int(cx - w / 2)
    img[int(top + 0.15 * h) : int(top + 0.6 * h), x1 : x1 + int(w * frac)] = color
    return img, ppl


def test_noisy_appearance_does_not_drop_lone_target():
    # С площадки: статичный кадр, расстояние по одежде у того же человека скачет 0.12–0.37 →
    # цель отвергалась, на её месте рождался «двойник» и перехватывал её навсегда.
    tr = PersonTracker()
    img, ppl = _partly_recolored(500, 200, RED, 0.0)
    target = next(x for x in tr.update(ppl, 0.0, img) if x.box[0] > 400)
    tr.select(target.id)
    t = 0.0
    for i in range(10):
        t += DT
        img, ppl = _partly_recolored(500, 200, GREEN, 0.4 if i % 2 == 0 else 0.0)
        tr.update(ppl, t, img)
        assert tr.target_state == "tracking" and tr.target_id == target.id
    assert len(tr.tracks) == 2  # двойник не появился


# --- резкое движение камеры (живая запись «Резко», 2026-10-29) ---------------------------------

W, H = 1280, 720


def shift_motion(dx_px: float, dy_px: float, t_move: float):
    """Картинка один раз сдвинулась на (dx, dy) px в момент t_move: motion(t0, t1) отвечает, как её двигала камера."""
    def motion(t0, t1):
        moved = t0 < t_move <= t1
        return 1.0, (dx_px / W if moved else 0.0), (dy_px / H if moved else 0.0)
    return motion


def person(cx, top, w=120, h=300, score=0.9):
    return Person(box=(cx - w / 2, top, cx + w / 2, top + h), score=score, keypoints=np.zeros((17, 3), np.float32))


def feed(tr, t, people, motion=None):
    return tr.update(people, t, None, frame_size=(W, H), motion=motion)


def test_camera_shift_does_not_lose_or_renumber_target():
    """Картинка прыгнула на 100 px: с поправкой на движение камеры цель — тот же трек, не потерян."""
    tr = PersonTracker()
    for i in range(5):
        feed(tr, i * DT, [person(500, 200)])
    tr.select(1)
    t_move = 5 * DT - 0.01
    motion = shift_motion(-100, -60, t_move)
    feed(tr, 5 * DT, [person(400, 140)], motion)
    assert tr.target_id == 1 and tr.target_state == "tracking"
    assert tr.target.hits == 6 and len(tr.tracks) == 1


def test_camera_shift_is_not_taken_for_walking_speed():
    """Скорость человека считается без сдвига от камеры: после рывка картинки он не «побежал»."""
    tr = PersonTracker()
    for i in range(5):
        feed(tr, i * DT, [person(500, 200)])
    tr.select(1)
    feed(tr, 5 * DT, [person(400, 140)], shift_motion(-100, -60, 5 * DT - 0.01))
    assert abs(tr.target.velocity[0]) < 100 and abs(tr.target.velocity[1]) < 100


def lose_target(tr, motion, n_missing=3):
    """Цель выбрана и стоит; затем детектор её не видит n_missing кадров (виден только сосед слева)."""
    for i in range(5):
        feed(tr, i * DT, [person(150, 200), person(600, 200)])
    tr.select(2)
    t = 5 * DT
    for _ in range(n_missing):
        feed(tr, t, [person(150, 200)], motion)
        t += DT
    return t


def test_target_adopts_new_track_at_predicted_place():
    tr = PersonTracker()
    motion = shift_motion(-100, -60, 5 * DT + 0.01)
    t = lose_target(tr, motion)
    assert tr.target_state == "lost"
    # через 0,2 с рядом с предсказанным местом (500, 140) — в стороне на 90 px, модель камеры неточна —
    # появляется новый трек почти того же размера: обычное сопоставление (IoU < 0,3) его не берёт, а переход по месту берёт
    feed(tr, t, [person(150, 200), person(410, 140, 124, 305)], motion)
    feed(tr, t + DT, [person(150, 200), person(410, 140, 124, 305)], motion)  # одного кадра мало (hits >= 2)
    assert tr.target_id == 2 and tr.target_state == "tracking"
    assert tr.target.box[0] > 300  # это новый человек справа, а не сосед
    ad = tr.pop_adoptions()
    assert len(ad) == 1 and ad[0]["how"] == "place" and ad[0]["id"] == 2 and ad[0]["from_id"] != 2
    assert tr.pop_adoptions() == []


def test_target_does_not_jump_to_other_person_in_view():
    """Сосед (старый трек) стоит на самом месте цели — цель к нему не переходит и остаётся потерянной."""
    tr2 = PersonTracker()
    for i in range(5):
        feed(tr2, i * DT, [person(300, 200), person(600, 200)])
    tr2.select(2)
    t = 5 * DT
    for _ in range(10):
        feed(tr2, t, [person(300, 200)])
        t += DT
    assert tr2.target_state == "lost" and tr2.target_id == 2 and tr2.pop_adoptions() == []


def test_no_adoption_when_new_track_too_late_wrong_size_or_wrong_place():
    for kind in ("late", "size", "place"):
        tr = PersonTracker()
        motion = shift_motion(-100, -60, 5 * DT + 0.01)
        t = lose_target(tr, motion)
        if kind == "late":
            t += 0.6
            newcomer = person(410, 140)
        elif kind == "size":
            newcomer = person(410, 140, 50, 120)
        else:
            newcomer = person(900, 400)
        feed(tr, t, [person(150, 200), newcomer], motion)
        feed(tr, t + DT, [person(150, 200), newcomer], motion)
        assert tr.target_state == "lost", kind
        assert tr.pop_adoptions() == [], kind


def test_predicted_shift_the_camera_did_not_make_gives_no_false_velocity():
    """Модель камеры обещает сдвиг, а картинка не двинулась (камера отстаёт): человек стоит, скорость ~0."""
    tr = PersonTracker()
    for i in range(5):
        feed(tr, i * DT, [person(500, 200)])
    tr.select(1)
    motion = shift_motion(-100, -60, 5 * DT - 0.01)
    for k in range(5, 8):
        feed(tr, k * DT, [person(500, 200)], motion)
    assert tr.target_state == "tracking"
    assert abs(tr.target.velocity[0]) < 5 and abs(tr.target.velocity[1]) < 5


# --- переход цели на новый трек: внешность и устойчивость ---------------------------------------


def _img_feed(tr, t, specs, motion=None):
    """specs: (cx, top, color[, green_frac]) — человек с долей рубашки другого цвета (фраза 0,5 -> расстояние ~0,54)."""
    img, ppl = scene(*[s[:3] for s in specs])
    for s in specs:
        if len(s) > 3:
            x1 = int(s[0] - 60)
            img[int(s[1] + 45) : int(s[1] + 180), x1 : x1 + int(120 * s[3])] = GREEN
    return tr.update(ppl, t, img, motion=motion)


def _lose_with_images(tr, motion):
    for i in range(5):
        _img_feed(tr, i * DT, [(150, 200, BLUE), (600, 200, RED)])
    tr.select(2)
    t = 5 * DT
    for _ in range(3):
        _img_feed(tr, t, [(150, 200, BLUE)], motion)
        t += DT
    return t


def test_no_adoption_of_newcomer_with_different_appearance():
    tr = PersonTracker()
    motion = shift_motion(-100, -60, 5 * DT + 0.01)
    t = _lose_with_images(tr, motion)
    for k in range(3):  # рядом с предсказанным местом (500, 140) другой по одежде человек: расстояние между 0,35 и 0,6
        _img_feed(tr, t + k * DT, [(150, 200, BLUE), (410, 140, RED, 0.5)], motion)
    assert tr.target_state == "lost" and tr.pop_adoptions() == []


def test_adoption_picks_similar_candidate_not_just_best_iou():
    tr = PersonTracker()
    motion = shift_motion(-100, -60, 5 * DT + 0.01)
    t = _lose_with_images(tr, motion)
    for k in range(3):  # (500, 320) — чуть ближе к месту (IoU 0,25), но другой по одежде; (410, 140) — хуже по месту, но наш
        _img_feed(tr, t + k * DT, [(150, 200, BLUE), (500, 320, RED, 0.3), (410, 140, RED)], motion)
    assert tr.target_state == "tracking" and tr.target.box[1] < 200
    ad = tr.pop_adoptions()
    assert len(ad) == 1


def test_one_frame_false_detection_is_not_adopted():
    tr = PersonTracker()
    motion = shift_motion(-100, -60, 5 * DT + 0.01)
    t = lose_target(tr, motion)
    feed(tr, t, [person(150, 200), person(410, 140, 124, 305)], motion)  # вспышка в один кадр
    assert tr.target_state == "lost" and tr.pop_adoptions() == []


# --- площадка 2026-11-13: группа на сцене, в кадре 4–12 человек ---


def test_target_prefers_the_strong_detection_over_a_weak_fragment():
    """Нейросеть дала на вокалиста две рамки: уверенную на всего человека и слабую (0,1) на его часть. Цель ушла на
    слабую, уверенная стала «новым человеком»; слабая через секунду пропала — и цель «потеряна» на 51 с, хотя человек
    всё это время был в кадре."""
    tr = PersonTracker()
    step(tr, 0.0, (400, 100, RED, 200, 580))
    tr.select_at(400, 300)
    for i in range(1, 12):  # 0,7 с: две рамки на одного человека; слабая — на «чистый» кусок одежды и по цвету
        # совпадает с целью точнее, чем уверенная (в ту попал сосед за спиной): так было в записи 2026-11-13
        step(tr, i * DT, (400, 100, (70, 70, 190), 200, 580), (420, 110, RED, 150, 580, 0.1))
    tgt = tr.target
    assert tr.target_state == "tracking"
    assert tgt.score >= 0.8 and tgt.box[2] - tgt.box[0] >= 190  # цель — на уверенной рамке
    assert len(tr.tracks) == 1  # и второго «человека» на его месте не появилось


def test_lost_target_is_not_recognised_across_the_frame():
    """Цель пропала, а через секунду похожий по одежде человек появился в другом конце кадра (музыканты в чёрном) —
    в записи цель за 4 с шесть раз «узнавалась» в разных людях, и камера бросалась за ними. Человек за секунду через
    весь кадр не переходит."""
    tr = PersonTracker()
    step(tr, 0.0, (300, 200, RED))
    tr.select_at(300, 300)
    for i in range(1, 6):
        step(tr, i * DT, (300, 200, RED))
    t = 6 * DT
    for _ in range(10):  # 0,7 с без цели
        tr.update([], t, np.full((720, 1280, 3), 25, np.uint8))
        t += DT
    for _ in range(6):  # такой же по одежде — в 800 пикселях от места потери
        step(tr, t, (1100, 200, RED))
        t += DT
    assert tr.target_state == "lost"
    for _ in range(6):  # а рядом с местом потери — узнаём
        step(tr, t, (340, 200, RED))
        t += DT
    assert tr.target_state == "tracking"
