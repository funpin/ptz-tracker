import numpy as np

from ptztracker.control.composition import COMPOSITIONS, DEFAULT_COMPOSITION, measure_body


def kp(nose=None, ls=None, rs=None, le=None, re=None, lh=None, rh=None):
    k = np.zeros((17, 3), np.float32)
    if lh:
        k[11] = (*lh, 0.9)
    if rh:
        k[12] = (*rh, 0.9)
    if nose:
        k[0] = (*nose, 0.9)
    if le:
        k[3] = (*le, 0.9)
    if re:
        k[4] = (*re, 0.9)
    if ls:
        k[5] = (*ls, 0.9)
    if rs:
        k[6] = (*rs, 0.9)
    return k


def test_measure_frontal():
    m = measure_body(kp((320, 100), (350, 160), (290, 160)), (260, 60, 380, 360), (640, 360), None)
    assert abs(m.nose[0] - 0.5) < 1e-6 and abs(m.nose[1] - 100 / 360) < 1e-6
    assert abs(m.u - 60 / 360) < 1e-6 and m.confident


def test_measure_profile_shoulders_only_uses_last_u():
    m = measure_body(kp(None, (350, 160), (330, 160)), (300, 60, 400, 360), (640, 360), last_u=0.15)
    assert m.u is None
    assert abs(m.nose[0] - 340 / 640) < 1e-6 and abs(m.nose[1] - (160 / 360 - 0.15)) < 1e-6


def test_measure_box_fallback():
    m = measure_body(kp(), (300, 60, 400, 360), (640, 360), last_u=0.1)
    assert not m.confident and abs(m.nose[0] - 350 / 640) < 1e-6 and abs(m.nose[1] - (60 / 360 + 0.1)) < 1e-6
    m2 = measure_body(kp(), (300, 60, 400, 360), (640, 360), last_u=None)
    assert abs(m2.nose[1] - (60 + 0.08 * 300) / 360) < 1e-6


def test_compositions_sane():
    assert DEFAULT_COMPOSITION == "waist_center"
    assert set(COMPOSITIONS) == {"waist_center", "waist_left", "waist_right", "full", "wide"}
    for c in COMPOSITIONS.values():
        assert 0 < c.anchor[0] < 1 and 0 < c.anchor[1] < 1 and c.u_frac > 0 and c.title
    assert COMPOSITIONS["waist_left"].anchor[0] < 0.5 < COMPOSITIONS["waist_right"].anchor[0]


def test_head_pitch_does_not_move_reference_or_size():
    # Докладчик смотрит в записи: нос опускается на 0.4U, расстояние нос→плечи сжимается.
    # Опорная точка и размер берутся от плеч — не меняются заметно.
    # реалистичные пропорции: ширина плеч ≈ 2.6·U
    up = measure_body(kp((320, 100), (398, 160), (242, 160)), (220, 60, 420, 360), (640, 360), None)
    down = measure_body(kp((320, 124), (398, 160), (242, 160)), (220, 60, 420, 360), (640, 360), up.u)
    assert abs(down.nose[1] - up.nose[1]) < 0.02 and abs(down.u / up.u - 1) < 0.25


def test_head_turn_does_not_move_reference_x():
    a = measure_body(kp((320, 100), (350, 160), (290, 160)), (260, 60, 380, 360), (640, 360), None)
    b = measure_body(kp((345, 100), (350, 160), (290, 160)), (260, 60, 380, 360), (640, 360), a.u)
    assert abs(b.nose[0] - a.nose[0]) < 0.005


def test_back_view_anchor_from_shoulders():
    # Докладчик спиной с самого начала: носа нет, last_u нет. Опорная точка должна лечь туда же,
    # где её дала бы фронтальная съёмка этой позы (нос на y=100, плечи на y=160, ширина плеч 156 = 2,6·U),
    # иначе кадр прыгнет, когда нос появится.
    box = (220, 60, 420, 360)
    size = (640, 360)
    shoulders = dict(ls=(398, 160), rs=(242, 160))
    frontal = measure_body(kp((320, 100), **shoulders), box, size, None)
    u_true = 60 / 360

    # с ушами: середина ушей ≈ высота носа
    ears = measure_body(kp(None, le=(345, 98), re=(295, 98), **shoulders), box, size, None)
    assert ears.confident
    assert abs(ears.nose[0] - frontal.nose[0]) < 1e-6
    assert abs(ears.nose[1] - frontal.nose[1]) < 0.3 * u_true
    assert ears.u is None  # уши со спины шумят — размер зуму не отдаём, только опорная точка

    # одно ухо (голова боком к камере) — тоже годится
    one_ear = measure_body(kp(None, re=(295, 98), **shoulders), box, size, None)
    assert one_ear.confident and abs(one_ear.nose[1] - frontal.nose[1]) < 0.3 * u_true

    # без ушей: U = ширина плеч · U_PER_SHOULDER; размер не отдаём — он угадан, зум по нему не ведём
    plain = measure_body(kp(None, **shoulders), box, size, None)
    assert plain.confident and plain.u is None
    assert abs(plain.nose[0] - frontal.nose[0]) < 1e-6
    assert abs(plain.nose[1] - frontal.nose[1]) < 0.3 * u_true
    # и это не «верх рамки + 8%» (прежний запасной вариант: y = 60+0,08·300 = 84 → ниже/выше на 0,3 U и более)
    assert abs(plain.nose[1] - (60 + 0.08 * 300) / 360) > 0.02

    # нелепые уши (высоко над головой) отбрасываются как не лезущие в разумные границы от sw_u
    silly = measure_body(kp(None, le=(345, 0), re=(295, 0), **shoulders), box, size, None)
    assert silly.u is None and abs(silly.nose[1] - frontal.nose[1]) < 0.3 * u_true

    # уши ниже, чем дал бы нос (U < ширины плеч) — не берём: у носа U не бывает меньше, иначе прыжок
    low = measure_body(kp(None, le=(345, 120), re=(295, 120), **shoulders), box, size, None)
    assert abs(low.nose[1] - plain.nose[1]) < 1e-6

    # last_u по-прежнему главнее
    kept = measure_body(kp(None, le=(345, 98), re=(295, 98), **shoulders), box, size, 0.1)
    assert abs(kept.nose[1] - (160 / 360 - 0.1)) < 1e-6 and kept.u is None


def test_torso_keeps_size_when_person_turns_sideways():
    """Живой прогон 2026-11-10: человек встал боком и наклонился — «нос → плечи» и ширина плеч сжались в 4–6 раз, зум
    наехал. Корпус «плечи → бёдра» при этом почти той же длины: он не даёт мерке упасть больше чем на 15 %."""
    box, frame = (500, 100, 780, 700), (1280, 720)
    front = measure_body(kp((640, 200), (720, 260), (560, 260), lh=(690, 458), rh=(590, 458)), box, frame, None)
    assert abs(front.u - 60 / 720) < 1e-6  # лицом: мерка прежняя — «нос → плечи» (корпус её не раздувает)
    side = measure_body(kp((650, 245), (655, 260), (625, 260), lh=(650, 458), rh=(630, 458)), box, frame, front.u)
    assert side.u >= 0.85 * front.u - 1e-6
    no_hips = measure_body(kp((650, 245), (655, 260), (625, 260)), box, frame, front.u)
    assert no_hips.u < 0.3 * front.u  # бёдер не видно — мерка прежняя (её страхует фильтр размера)


def test_hips_guessed_below_the_frame_do_not_inflate_size():
    """На «Поясе» бёдра за нижним краем кадра, и точки там — догадка нейросети: по ним корпус не меряем."""
    box, frame = (500, 100, 780, 720), (1280, 720)
    m = measure_body(kp((640, 200), (720, 260), (560, 260), lh=(690, 900), rh=(590, 900)), box, frame, None)
    assert abs(m.u - 60 / 720) < 1e-6
