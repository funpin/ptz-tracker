"""Команды --lab без окна: запись, слежение, калибровка на симуляторе."""

import json
from pathlib import Path

import pytest

import ptztracker.lab as lab
from ptztracker.__main__ import main
from ptztracker.camera.session import CameraSession
from ptztracker.recorder.reader import Episode

SELECT = "516,374"  # человек синтетической сцены в начальном положении (кадр 1280×720)


@pytest.fixture
def sessions(monkeypatch):
    """Запоминает созданные лабораторией сессии, чтобы смотреть команды, дошедшие до симулятора."""
    made = []

    class Spy(CameraSession):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            made.append(self)

    monkeypatch.setattr(lab, "CameraSession", Spy)
    return made


def result_of(out: str) -> dict:
    line = [ln for ln in out.splitlines() if ln.startswith("RESULT ")][-1]
    return json.loads(line[len("RESULT "):])


def moving(cmds):
    return [c for c in cmds if c.startswith(("#PTS", "#Z", "#APS", "#AXZ", "#AXI")) and c not in ("#PTS5050", "#Z50")]


@pytest.mark.parametrize("cmd", ["follow", "calibrate"])
def test_lab_movement_requires_not_on_air(cmd, sessions, capsys):
    rc = main(["--lab", cmd, "--ip", "sim", "--select", SELECT])
    assert rc == 3
    out = capsys.readouterr().out
    assert "не в эфире" in out.lower()
    res = result_of(out)
    assert res["error"] == "not_on_air" and res["exit_code"] == 3
    assert sessions == []  # до подключения


def test_lab_record_sim_writes_zip(tmp_path, sessions, capsys):
    rc = main(["--lab", "record", "--ip", "sim", "--seconds", "2", "--select", SELECT, "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0, out
    res = result_of(out)
    ep = Episode.open(res["zip"])
    assert ep.duration > 1
    assert str(tmp_path) in res["zip"]
    # запись камеру не двигает
    assert moving(list(sessions[0].sim.received)) == []


def stops_and_moves(cmds):
    return [c for c in cmds if c.startswith(("#PTS", "#Z", "#F", "#APS", "#AXZ", "#AXI", "#AXF"))]


def test_lab_record_sends_only_queries(tmp_path, sessions, capsys):
    """record — второй клиент к камере в эфире: ни движений, ни стопов (стоп заморозил бы камеру)."""
    rc = main(["--lab", "record", "--ip", "sim", "--seconds", "2", "--select", SELECT, "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0, out
    ep = Episode.open(result_of(out)["zip"])
    cmds = list(ep.events("cmd"))
    assert cmds and {e["priority"] for e in cmds} == {"QUERY"}
    assert stops_and_moves(list(sessions[0].sim.received)) == []


def test_lab_record_ctrl_c_sends_no_stop(tmp_path, sessions, monkeypatch, capsys):
    def boom(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(lab, "_sleep", boom)
    rc = main(["--lab", "record", "--ip", "sim", "--seconds", "30", "--out", str(tmp_path)])
    assert rc == 130
    assert stops_and_moves(list(sessions[0].sim.received)) == []
    res = result_of(capsys.readouterr().out)
    assert res["interrupted"] is True
    assert Path(res["zip"]).exists()


def test_lab_follow_ctrl_c_sends_stop(tmp_path, sessions, monkeypatch, capsys):
    def boom(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(lab, "_sleep", boom)
    monkeypatch.setattr(lab, "_select_person", lambda s, xy: boom(0))
    rc = main(["--lab", "follow", "--ip", "sim", "--seconds", "30", "--select", SELECT, "--not-on-air",
               "--out", str(tmp_path)])
    assert rc == 130
    received = list(sessions[0].sim.received)
    assert "#PTS5050" in received and "#Z50" in received
    assert result_of(capsys.readouterr().out)["interrupted"] is True


def test_lab_select_without_person_exits_4(tmp_path, sessions, monkeypatch, capsys):
    monkeypatch.setattr(lab, "SELECT_WAIT_S", 0.6)
    rc = main(["--lab", "record", "--ip", "sim", "--seconds", "2", "--select", "1,1", "--out", str(tmp_path)])
    assert rc == 4
    assert "человек" in capsys.readouterr().out.lower()


def test_lab_bad_composition_exits_2(tmp_path, sessions):
    rc = main(["--lab", "follow", "--ip", "sim", "--not-on-air", "--composition", "нет", "--out", str(tmp_path)])
    assert rc == 2


def test_lab_rejects_non_positive_seconds(sessions, capsys):
    assert main(["--lab", "record", "--ip", "sim", "--seconds", "0"]) == 2
    assert result_of(capsys.readouterr().out)["error"] == "bad_args"
    assert sessions == []


def test_lab_output_hides_password_and_rtsp_urls(tmp_path, sessions, monkeypatch, capsys):
    """Исключение с адресом потока и пароль не попадают ни в текст, ни в RESULT."""
    orig_start = CameraSession.start

    def failing_start(self):
        orig_start(self)
        raise RuntimeError("поток rtsp://u:secret@host/live не открылся, пароль secret2")

    monkeypatch.setattr(CameraSession, "start", failing_start)
    cam = lab.CameraConfig(id="sim", name="s", ip="sim", kind="sim")
    monkeypatch.setattr(lab, "_resolve_camera", lambda ip, user: (cam, lab.AppConfig(), "secret2"))
    rc = main(["--lab", "record", "--ip", "sim", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "secret" not in out
    assert result_of(out)["error"]


@pytest.mark.slow
def test_lab_follow_sim_prints_summary(tmp_path, sessions, capsys):
    rc = main(["--lab", "follow", "--ip", "sim", "--seconds", "6", "--select", SELECT, "--composition",
               "waist_center", "--effort", "0.5", "--not-on-air", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "Слежение:" in out  # сводка повтора
    res = result_of(out)
    assert res["summary"]["following_s"] > 3
    assert moving(list(sessions[0].sim.received))


@pytest.mark.slow
def test_lab_calibrate_sim_prints_json(tmp_path, sessions, capsys, monkeypatch):
    monkeypatch.setattr(lab, "default_config_path", lambda: tmp_path / "config.json")
    rc = main(["--lab", "calibrate", "--ip", "sim", "--not-on-air", "--save", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0, out
    res = result_of(out)
    assert res["calibration"]
    assert "saved" not in res  # симулятора в настройках нет — конфиг не трогаем
