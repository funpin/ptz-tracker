from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.control.zones import SCALE_RANGE, ZONE_DEAD, ZONE_SOFT, Zone, framing, zone_hint, zone_of


def test_zone_clamps_point_and_scale():
    z = Zone.clamped(1.5, -1.0, 9.0, 0.01)
    assert (z.x, z.y) == (0.9, 0.1)
    assert (z.w, z.h) == (SCALE_RANGE[1], SCALE_RANGE[0])  # ширина и высота — каждая в своих пределах
    assert Zone.clamped(0.4, 0.3, 0.01) == Zone(0.4, 0.3, SCALE_RANGE[0], SCALE_RANGE[0])  # одно число — оба размера


def test_soft_zone_never_wider_than_frame():
    e = EffortProfile.at(0.0)  # самая широкая мягкая зона
    _, scaled = framing("waist_center", 0.0, {"waist_center": [0.5, 0.25, SCALE_RANGE[1]]})
    assert scaled.soft_x <= 0.5 and scaled.soft_y <= 0.5
    assert scaled.soft_x > e.soft_x


def test_framing_without_zone_is_the_preset():
    comp, e = framing("wide", 0.5, {})
    assert comp == COMPOSITIONS["wide"]
    usual = EffortProfile.at(0.5)  # «Обычно»: обычная зона — та же, что была у этого характера
    assert all(getattr(e, f) == getattr(usual, f) for f in ("dead_x", "dead_y", "soft_x", "soft_y", "gain", "v_max",
                                                            "a_max", "j_max", "zoom_dead", "zoom_gain", "zoom_max"))
    assert (e.watch_soft_x, e.watch_dead_x) == (usual.soft_x, usual.dead_x)  # и у сторожа раскачки — те же пороги


def test_framing_moves_point_and_scales_both_zones():
    comp, e = framing("waist_center", 0.5, {"waist_center": [0.45, 0.45, 1.5]})
    base = EffortProfile.at(0.5)
    assert comp.anchor == (0.45, 0.45)
    assert comp.key == "waist_center" and comp.u_frac == COMPOSITIONS["waist_center"].u_frac
    assert abs(e.dead_x - base.dead_x * 1.5) < 1e-9 and abs(e.soft_y - base.soft_y * 1.5) < 1e-9
    assert e.gain == base.gain  # характер движения не меняется


def test_zone_is_per_framing_size():
    zones = {"wide": [0.6, 0.4, 1.0]}
    assert framing("waist_center", 0.5, zones)[0] == COMPOSITIONS["waist_center"]
    assert framing("wide", 0.5, zones)[0].anchor == (0.6, 0.4)


def test_zone_of_ignores_garbage():
    assert zone_of({"wide": "x"}, "wide") is None
    assert zone_of({"wide": [0.5]}, "wide") is None
    assert zone_of({"wide": [0.5, 0.5, "a"]}, "wide") is None
    assert zone_of(None, "wide") is None
    assert zone_of({"wide": [2, 0.5, 1]}, "wide") == Zone(0.9, 0.5, 1.0)


# --- этап 2: зона — граница; ширина и высота отдельно (вариант Б, решение оператора 2026-11-11) ---


def test_zone_size_no_longer_depends_on_effort():
    """«Мягко … Резко» отвечает за плавность, а размер зоны задаёт только оператор на видео."""
    for key in ("waist_center", "full", "wide"):
        sizes = {tuple(getattr(framing(key, eff, {})[1], f) for f in ("dead_x", "dead_y", "soft_x", "soft_y"))
                 for eff in (0.0, 0.5, 1.0)}
        assert len(sizes) == 1, (key, sizes)
    soft, sharp = framing("waist_center", 0.0, {})[1], framing("waist_center", 1.0, {})[1]
    assert (soft.dead_x, soft.soft_x) == (ZONE_DEAD[0], ZONE_SOFT[0]) == (0.05, 0.22)
    assert soft.gain < sharp.gain and soft.a_max < sharp.a_max and soft.zoom_max < sharp.zoom_max


def test_zone_width_and_height_are_separate():
    """Широко по сцене, узко по высоте: ширина и высота зоны — два числа."""
    _, e = framing("waist_center", 0.5, {"waist_center": [0.5, 0.45, 1.5, 0.5]})
    assert abs(e.soft_x - 0.22 * 1.5) < 1e-9 and abs(e.soft_y - 0.18 * 0.5) < 1e-9
    assert abs(e.dead_x - 0.05 * 1.5) < 1e-9 and abs(e.dead_y - 0.045 * 0.5) < 1e-9
    assert (e.zone_w, e.zone_h) == (1.5, 0.5)  # размер зоны оператора — для порогов «идёт / стоит»


def test_legacy_zone_record_is_read():
    """Запись прежних версий [x, y, масштаб] читается как одинаковые ширина и высота; новая — [x, y, ширина, высота]."""
    assert zone_of({"wide": [0.6, 0.4, 1.3]}, "wide") == Zone(0.6, 0.4, 1.3, 1.3)
    assert zone_of({"wide": [0.6, 0.4, 1.3, 0.7]}, "wide") == Zone(0.6, 0.4, 1.3, 0.7)
    assert Zone(0.6, 0.4, 1.3, 0.7).to_json() == [0.6, 0.4, 1.3, 0.7]
    assert zone_of({"wide": [0.6, 0.4, 1.3, 0.7, 1.0]}, "wide") is None
    assert zone_of({"wide": [0.6, 0.4, 1.3, "x"]}, "wide") is None


def test_zone_is_cut_by_frame_edge():
    """Граница зоны не дальше края кадра минус 0,06: «Пояс · слева» с самой широкой зоной — 0,32, а не 0,396."""
    _, e = framing("waist_left", 0.5, {"waist_left": [0.38, 0.45, 1.8]})
    assert abs(e.soft_x - (0.38 - 0.06)) < 1e-9
    assert abs(e.soft_y - 0.18 * 1.8) < 1e-9  # по высоте до края далеко — не обрезана
    assert e.zone_w == 1.8  # размер, который задал оператор, не меняется: обрезана только граница
    # точка у самого края кадра: граница не становится меньше покоя
    _, e = framing("waist_left", 0.5, {"waist_left": [0.10, 0.45, 1.0]})
    assert e.soft_x > e.dead_x > 0
    # без своей зоны «Пояс» и «Общий» остаются как были, «Рост» (точка в 0,17 от верха) — обрезан по верху
    assert framing("waist_center", 0.5, {})[1].soft_y == 0.18 and framing("wide", 0.5, {})[1].soft_y == 0.18
    assert abs(framing("full", 0.5, {})[1].soft_y - (0.17 - 0.06)) < 1e-9


def test_zone_hint_names_what_the_camera_can_hold():
    """Плашка зоны говорит оператору, что эта зона значит для этой камеры и потока."""
    fast_camera, slow_stream = 0.31, 0.66  # «позднее знание» T: HE40 на MJPEG (1) и HE42 на MJPEG (2)
    # «хватает», а не «удержит»: на стенде и в обычной зоне человек 12 % времени за границей (тот, кто шатался у
    # края зоны и пошёл, выходит на 0,16–0,20) — плашка не должна обещать больше, чем делает камера
    assert zone_hint(0.22, 0.18, fast_camera) == "Зона ±22 % × ±18 % · хватает на спокойный шаг"
    assert zone_hint(0.11, 0.09, fast_camera) == "Зона ±11 % × ±9 % · плотнее, чем успевает камера"
    assert zone_hint(0.22, 0.18, slow_stream) == "Зона ±22 % × ±18 % · плотнее, чем успевает камера"
    assert zone_hint(0.44, 0.10, fast_camera) == "Зона ±44 % × ±10 % · хватает и на быстрый шаг"


def test_zone_of_survives_absurd_numbers():
    """Испорченный config.json с числом в сотни цифр: запись зоны отбрасывается, программа не падает на старте."""
    assert zone_of({"wide": [0.5, 0.5, 10**400]}, "wide") is None
    assert zone_of({"wide": [10**400, 0.5, 1, 1]}, "wide") is None


def test_swing_watchdog_thresholds_are_not_looser_than_before():
    """Сторож раскачки сравнивает пик ошибки с границей зоны, а «человек стоит» — с покоем. Зона больше не зависит от
    характера, но сторож от этого слабее стать не должен: его граница — не больше прежней (у «Резко» было 0,18),
    его покой — не меньше прежнего (у «Мягко» было 0,07). Размер зоны оператора умножает их, как и раньше."""
    for eff in (0.0, 0.5, 1.0):
        old = EffortProfile.at(eff)
        for w in (0.5, 1.0, 1.8):
            _, e = framing("waist_center", eff, {"waist_center": [0.5, 0.45, w]})
            assert e.watch_soft_x <= min(e.soft_x, old.soft_x * w) + 1e-9, (eff, w)
            assert e.watch_soft_y <= min(e.soft_y, old.soft_y * w) + 1e-9, (eff, w)
            assert e.watch_dead_x >= max(e.dead_x, old.dead_x * w) - 1e-9, (eff, w)
            assert e.watch_dead_y >= max(e.dead_y, old.dead_y * w) - 1e-9, (eff, w)
    sharp = framing("waist_center", 1.0, {})[1]
    assert (sharp.soft_x, sharp.watch_soft_x) == (0.22, 0.18)
