"""Замедление поворота на теле (AW-HE40, клуб 2026-10-30): выше 0xD54 °/с по коду идут по he42.TELE_PAN_DEG_S."""

import math

from ptztracker.camera import he42


def test_tele_ratio_is_one_up_to_d54():
    for z in (0x555, 0x800, 0xAAA, 0xD54):
        for code in (53, 63, 70, 99):
            assert he42.tele_pan_ratio(code, z) == 1.0, (hex(z), code)


def test_tele_ratio_matches_measured_tables_exactly():
    for z, table in he42.TELE_PAN_DEG_S.items():
        for code, deg in table.items():
            want = deg / he42.pan_base_deg_s(code)
            assert abs(he42.tele_pan_ratio(code, z) - want) < 1e-9, (hex(z), code)


def test_pan_deg_s_at_full_tele():
    # 0xFFF: код 70 — 2,47 °/с (замер), код 63 — 1,20; медленные 53–55 не замедляются
    assert abs(he42.pan_deg_s(70, 0xFFF) - 2.47) < 1e-6
    assert abs(he42.pan_deg_s(63, 0xFFF) - 1.20) < 1e-6
    assert abs(he42.pan_deg_s(55, 0xFFF) - 0.32) < 1e-6
    assert abs(he42.pan_deg_s(53, 0xFFF) - he42.PAN_DEG_S[53]) < 1e-9
    assert abs(he42.pan_deg_s(70, 0xD54) - he42.PAN_DEG_S[70]) < 1e-9


def test_codes_missing_from_table_interpolate_across_codes():
    # 0xEA9: замерены 55, 63, 75; код 70 — между 63 и 75, значит между их долями замедления
    r63, r75 = he42.tele_pan_ratio(63, 0xEA9), he42.tele_pan_ratio(75, 0xEA9)
    r70 = he42.tele_pan_ratio(70, 0xEA9)
    assert min(r63, r75) < r70 < max(r63, r75)
    # быстрее самого быстрого замеренного кода доля замедления держится
    assert he42.tele_pan_ratio(99, 0xFFF) == he42.tele_pan_ratio(75, 0xFFF)


def test_monotone_slowdown_between_d54_and_fff_in_log_deg_s():
    prev = he42.pan_deg_s(75, 0xD54)
    for z in range(0xD60, 0x1000, 0x20):
        cur = he42.pan_deg_s(75, min(z, 0xFFF))
        assert cur <= prev + 1e-12, hex(z)
        prev = cur
    # линейно по логарифму °/с между D54 и EA9
    mid = (0xD54 + 0xEA9) // 2
    lo, hi = math.log(he42.pan_deg_s(75, 0xD54)), math.log(he42.pan_deg_s(75, 0xEA9))
    assert abs(math.log(he42.pan_deg_s(75, mid)) - (lo + hi) / 2) < 0.01
