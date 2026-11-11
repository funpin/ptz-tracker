import logging

import pytest

from ptztracker.camera import he42, models
from ptztracker.camera.models import profile_for
from ptztracker.config import CameraConfig


@pytest.fixture(autouse=True)
def _fresh_warnings():
    models._warned.clear()
    yield
    models._warned.clear()


def test_known_models():
    he42p, he40p = profile_for("AW-HE42"), profile_for("AW-HE40")
    assert (he42p.name, he40p.name) == ("AW-HE42", "AW-HE40")
    assert he40p.image_controls_verified is True  # gain/WB/питание пробованы на живой HE40 (§7)
    assert he42p.image_controls_verified is False


def test_he40_profile_has_measured_response():
    # клуб 2026-11-10: HE40 слушается за 0,05–0,07 с и после «стоп» проходит v·0,03–0,07 с — втрое быстрее модели HE42
    he42p, he40p = profile_for("AW-HE42"), profile_for("AW-HE40")
    assert (he40p.dead_time, he40p.tau) == (0.05, 0.05)
    assert (he42p.dead_time, he42p.tau) == (he42.DEAD_TIME_S, he42.RAMP_TAU_S) == (0.08, 0.12)


def test_model_name_is_matched_loosely():
    assert profile_for(" aw-he40 ").name == "AW-HE40"
    assert profile_for("AW-HE40SKEJ").name == "AW-HE40"  # у моделей Panasonic бывают суффиксы
    assert profile_for("AW-HE42KE").name == "AW-HE42"


def test_both_models_slow_down_at_tele_with_the_same_measured_table():
    for name in ("AW-HE42", "AW-HE40"):
        p = profile_for(name)
        assert p.tele_pan_deg_s is he42.TELE_PAN_DEG_S
        assert p.tele_start_gz == he42.TELE_START_GZ


def test_he40_speed_note_is_informational_only():
    p40, p42 = profile_for("AW-HE40"), profile_for("AW-HE42")
    assert p40.speed_note and "70" in p40.speed_note and "17" in p40.speed_note
    assert p42.speed_note == ""
    assert p40.pan_deg_s_codes == p42.pan_deg_s_codes == tuple(sorted(he42.PAN_DEG_S))  # одна таблица на обе


def test_default_min_interval_matches_config_default():
    for name in ("AW-HE42", "AW-HE40"):
        assert profile_for(name).min_interval == CameraConfig(id="a", name="a", ip="1.1.1.1").min_interval == 0.130


@pytest.mark.parametrize("model", ["", "AW-UE150", "что-то"])
def test_unknown_model_gets_he42_and_warns_once(model, caplog):
    with caplog.at_level(logging.WARNING, logger="ptztracker.camera.models"):
        assert profile_for(model).name == "AW-HE42"
        assert profile_for(model).name == "AW-HE42"
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_not_asked_yet_is_not_unknown_no_warning(caplog):
    """None — QID ещё не отправляли или ответа нет: профиль по умолчанию без предупреждения в журнале."""
    with caplog.at_level(logging.WARNING, logger="ptztracker.camera.models"):
        assert profile_for(None).name == "AW-HE42"
    assert not caplog.records


def test_known_model_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="ptztracker.camera.models"):
        profile_for("AW-HE40")
    assert not caplog.records
