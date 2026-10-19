import numpy as np

from ptztracker.control.composition import COMPOSITIONS, DEFAULT_COMPOSITION, measure_body


def kp(nose=None, ls=None, rs=None):
    k = np.zeros((17, 3), np.float32)
    if nose:
        k[0] = (*nose, 0.9)
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
