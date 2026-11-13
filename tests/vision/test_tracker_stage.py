"""Область сцены в трекере: сама программа берёт цель только среди людей на сцене (флаги on_stage)."""

from test_tracker import BLUE, DT, RED, H, W, lose_target, person, scene

from ptztracker.vision.tracker import PersonTracker


def step(tr, t, flags, *people):
    img, ppl = scene(*people)
    return tr.update(ppl, t, img, on_stage=flags)


def lose_red(tr):
    red = step(tr, 0.0, [True], (300, 200, RED))[0]
    tr.select(red.id)
    t = 0.0
    for _ in range(46):  # ~3 с — трек удалён
        t += DT
        step(tr, t, [])
    return red, t + DT


def test_lost_target_is_not_recognised_by_appearance_outside_area():
    tr = PersonTracker()
    red, t = lose_red(tr)
    step(tr, t, [False, True], (350, 300, RED), (900, 200, BLUE))  # похожий человек — в зале
    assert tr.target_id == red.id and tr.target_state == "lost" and tr.pop_adoptions() == []


def test_lost_target_is_recognised_on_stage():
    tr = PersonTracker()
    red, t = lose_red(tr)
    step(tr, t, [True, True], (350, 300, RED), (900, 200, BLUE))
    assert tr.target_state == "tracking" and tr.target.box[0] < 600


def _feed(tr, t, people, flags):
    return tr.update(people, t, None, frame_size=(W, H), on_stage=flags)


def test_lost_target_is_not_adopted_by_place_outside_area():
    """Новый трек рядом с местом цели, но его голова вне области (зритель встал перед сценой) — цель не его."""
    for flag, state in ((False, "lost"), (True, "tracking")):
        tr = PersonTracker()
        t = lose_target(tr, None)
        for k in range(2):  # одного кадра мало (hits >= 2)
            _feed(tr, t + k * DT, [person(150, 200), person(690, 200, 124, 305)], [True, flag])
        assert tr.target_state == state, flag


def test_flags_are_kept_on_tracks():
    tr = PersonTracker()
    seen = step(tr, 0.0, [True, False], (300, 200, RED), (900, 200, BLUE))
    assert sorted((round(x.box[0]), x.on_stage) for x in seen) == [(240, True), (840, False)]
    seen = step(tr, DT, [False, False], (300, 200, RED), (900, 200, BLUE))
    assert [x.on_stage for x in seen] == [False, False]


def test_without_flags_everyone_is_on_stage():
    tr = PersonTracker()
    img, ppl = scene((300, 200, RED))
    assert tr.update(ppl, 0.0, img)[0].on_stage is True


def _lead(tr, flags_last=True):
    """Цель стоит на сцене: рамка 120×300 с верхом на 200, голова — (500, 224)."""
    for i in range(5):
        step(tr, i * DT, [True], (500, 200, RED))
    tr.select(1)
    return 5 * DT


def test_led_target_does_not_jump_to_box_outside_area():
    """Цель закрыли, а на её месте — рамка зрителя: голова в 0,4 высоты рамки от прежней и вне области. Не берём."""
    tr = PersonTracker()
    t = _lead(tr)
    step(tr, t, [False], (500, 320, RED))
    assert tr.target_id == 1 and tr.target_state == "lost"


def test_same_jump_inside_area_is_followed_as_before():
    tr = PersonTracker()
    t = _lead(tr)
    step(tr, t, [True], (500, 320, RED))
    assert tr.target_state == "tracking"


def test_own_box_on_stage_wins_over_box_outside_area():
    tr = PersonTracker()
    t = _lead(tr)
    seen = step(tr, t, [False, True], (500, 320, RED), (505, 205, RED))
    assert tr.target_state == "tracking" and tr.target.box[1] == 205
    assert len(seen) == 2


def test_led_target_walks_out_of_area():
    """Вокалист спустился в зал: шаг за шагом, без скачка — его ведём и за рамкой, сколько бы он там ни был."""
    tr = PersonTracker()
    t = _lead(tr)
    for i in range(1, 40):
        step(tr, t, [i < 5], (500 + 6 * i, 200 + 8 * i, RED))
        assert tr.target_state == "tracking", i
        t += DT
    step(tr, t, [False], (500 + 6 * 39, 200 + 8 * 39 + 110, RED))  # уже вне области — скачок не проверяем
    assert tr.target_state == "tracking"


def test_without_area_jump_is_followed_as_before():
    tr = PersonTracker()
    for i in range(5):
        tr.update(scene((500, 200, RED))[1], i * DT, scene((500, 200, RED))[0])
    tr.select(1)
    img, ppl = scene((500, 320, RED))
    tr.update(ppl, 5 * DT, img)
    assert tr.target_state == "tracking"


def test_adoption_record_says_whether_it_was_on_stage():
    tr = PersonTracker()
    red, t = lose_red(tr)
    step(tr, t, [True], (350, 300, RED))
    assert tr.pop_adoptions()[0]["stage"] is True
