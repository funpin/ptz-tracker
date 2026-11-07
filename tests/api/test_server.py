import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from ptztracker.api.server import HOST, ApiError, ApiServer, QtInvoker
from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow

PASSWORD = "Sim-Secret-42"


def call(port, path, token=None, body=None, method=None):
    """HTTP-запрос к API → (код, тело). Ошибки HTTP тоже возвращаются, а не бросаются."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://{HOST}:{port}{path}", data=data, method=method or ("POST" if body is not None else "GET"))
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def call_bg(qtbot, *args, **kw):
    """Запрос из чужого потока, пока поток Qt крутит события (действия выполняются в нём)."""
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", call(*args, **kw)), daemon=True)
    t.start()
    qtbot.waitUntil(lambda: "r" in out, timeout=10000)
    code, raw = out["r"]
    return code, json.loads(raw) if raw[:1] == b"{" else raw


class FakeBackend:
    def status(self):
        return {"ok": True}

    def events(self, since, limit):
        return {"since": since, "limit": limit, "events": []}

    def frame_jpeg(self):
        return None

    def save_moment(self):
        return {"path": "x.zip"}

    def control(self, name, body):
        raise ApiError(403, "нельзя")


@pytest.fixture
def server():
    srv = ApiServer(FakeBackend(), "tok-123", 0)
    srv.start()
    yield srv
    srv.stop()


def test_status_requires_token(server):
    assert call(server.port, "/status")[0] == 401
    assert call(server.port, "/status", token="wrong")[0] == 401
    code, raw = call(server.port, "/status", token="tok-123")
    assert code == 200 and json.loads(raw) == {"ok": True}


def test_empty_token_rejects_everything():
    srv = ApiServer(FakeBackend(), "", 0)
    srv.start()
    try:
        assert call(srv.port, "/status", token="")[0] == 401
    finally:
        srv.stop()


def test_api_binds_localhost_only(server):
    host, port = server.address
    assert host == "127.0.0.1" and port > 0
    # слушающий сокет привязан именно к петлевому адресу, а не к 0.0.0.0
    assert server._httpd.socket.getsockname()[0] == "127.0.0.1"


def test_bad_requests(server):
    t = "tok-123"
    assert call(server.port, "/nope", token=t)[0] == 404
    assert call(server.port, "/status", token=t, body={})[0] == 405
    assert call(server.port, "/events?since=abc", token=t)[0] == 400
    code, raw = call(server.port, "/events?since=12.5&limit=99999", token=t)
    assert code == 200 and json.loads(raw)["limit"] == 2000
    assert call(server.port, "/frame.jpg", token=t)[0] == 503


def test_rejected_post_does_not_poison_keep_alive(server):
    import http.client

    c = http.client.HTTPConnection(HOST, server.port, timeout=5)
    c.request("POST", "/follow", body=b'{"on": true}', headers={"Authorization": "Bearer wrong"})
    r = c.getresponse()
    assert r.status == 401
    r.read()
    # тело первого запроса не прочитано — сервер закрывает соединение, клиент открывает новое
    c.request("GET", "/status", headers={"Authorization": "Bearer tok-123"})
    r = c.getresponse()
    assert r.status == 200 and json.loads(r.read()) == {"ok": True}
    c.close()


def test_port_busy_raises():
    busy = socket.socket()
    busy.bind((HOST, 0))
    busy.listen(1)
    try:
        with pytest.raises(OSError):
            ApiServer(FakeBackend(), "t", busy.getsockname()[1]).start()
    finally:
        busy.close()


def test_invoker_runs_in_qt_thread_and_cancels_on_timeout(qtbot):
    inv = QtInvoker()
    qt_thread = threading.current_thread()
    seen = []

    out = {}
    t = threading.Thread(target=lambda: out.setdefault("v", inv.call(lambda: seen.append(threading.current_thread()) or 7)))
    t.start()
    qtbot.waitUntil(lambda: "v" in out, timeout=3000)
    assert out["v"] == 7 and seen == [qt_thread]

    # поток Qt занят дольше таймаута: ответ 504, и действие потом не выполняется
    late = []

    def bg():
        try:
            inv.call(lambda: late.append(1), timeout=0.2)
        except ApiError as e:
            out["err"] = e.status

    t = threading.Thread(target=bg)
    t.start()
    time.sleep(0.5)  # поток Qt не обрабатывает события
    t.join(2)
    qtbot.wait(100)
    assert out["err"] == 504 and late == []


# --- API настоящего окна (симулятор) -----------------------------------------


def make_window(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.cfg.record_dir = str(tmp_path / "rec")
    w.set_cell_camera(w.grid.selected, w._current_cfg().id)
    w.show()
    assert w._api is not None
    return w, w._api.port, w.cfg.api_token


def pick_person(qtbot, w):
    qtbot.waitUntil(lambda: (r := w.session.analyzer.latest()) is not None and bool(r.tracks), timeout=5000)
    x1, y1, x2, y2 = w.session.analyzer.latest().tracks[0].box
    return {"x": (x1 + x2) / 2, "y": (y1 + y2) / 2}


def test_status_has_no_password(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    s = w.session
    s.cfg.username = "admin"
    s.cfg.rtsp_url = f"rtsp://admin:{PASSWORD}@10.0.0.12/MediaInput/h264/stream_1"
    st = s.frames.stats()
    st.error = f"Нет видео rtsp://admin:{PASSWORD}@10.0.0.12/MediaInput/h264: таймаут"
    s.frames.stats = lambda: st
    code, raw = call(port, "/status", token=tok)
    assert code == 200
    assert PASSWORD.encode() not in raw and tok.encode() not in raw
    data = json.loads(raw)
    assert data["camera"]["id"] == "sim1" and "rtsp_url" not in data["camera"]
    assert "admin:***@" in data["video"]["error"]
    assert data["agent"] == {"control_allowed": False, "not_on_air": False}
    w.close()


def test_control_forbidden_by_default(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    for path, body in [("/follow", {"on": True}), ("/target", {"x": 1, "y": 2}),
                       ("/composition", {"key": "full"}), ("/effort", {"value": 0.3}), ("/calibrate", {})]:
        code, data = call_bg(qtbot, port, path, token=tok, body=body)
        assert code == 403, path
    assert not w.session.follower.enabled
    w.close()


MOVING = [("/follow", {"on": True}), ("/target", {"x": 1, "y": 2}),
          ("/composition", {"key": "full"}), ("/effort", {"value": 0.3}), ("/calibrate", {})]


def test_moving_needs_not_on_air_but_stop_does_not(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    w.agent_panel.allow_check.setChecked(True)  # разрешено, но камера может быть в эфире
    for path, body in MOVING:
        code, data = call_bg(qtbot, port, path, token=tok, body=body)
        assert code == 403 and "НЕ в эфире" in data["error"], path
    f = w.session.follower
    f.enabled = True  # слежение включил оператор; агент может его выключить — это стоп
    code, data = call_bg(qtbot, port, "/follow", token=tok, body={"on": False})
    assert code == 200 and not data["following"] and not f.enabled
    w.close()


def test_agent_selects_target_and_follows_through_enable(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    w.agent_panel.allow_check.setChecked(True)
    w.agent_panel.off_air_check.setChecked(True)
    code, data = call_bg(qtbot, port, "/follow", token=tok, body={"on": True})
    assert code == 409 and "цель" in data["error"]  # enable() отказал: цели нет
    code, data = call_bg(qtbot, port, "/target", token=tok, body=pick_person(qtbot, w))
    assert code == 200 and data["target_id"] is not None
    code, data = call_bg(qtbot, port, "/follow", token=tok, body={"on": True})
    assert code == 200 and data["following"] and w.session.follower.enabled
    qtbot.waitUntil(lambda: w.follow_panel.follow_btn.isChecked(), timeout=1000)
    code, data = call(port, "/status", token=tok)
    assert json.loads(data)["following"] is True
    code, data = call_bg(qtbot, port, "/follow", token=tok, body={"on": False})
    assert code == 200 and not data["following"] and not w.session.follower.enabled
    assert w.agent_panel.control_allowed  # выключил сам агент — разрешение остаётся
    w.close()


def test_off_air_untick_stops_agent_follow_only(qtbot, tmp_path):
    """Оператор снял «Камера НЕ в эфире»: слежение, включённое агентом, выключается; своё — нет."""
    w, port, tok = make_window(qtbot, tmp_path)
    p, f = w.agent_panel, w.session.follower
    p.allow_check.setChecked(True)
    p.off_air_check.setChecked(True)
    assert call_bg(qtbot, port, "/target", token=tok, body=pick_person(qtbot, w))[0] == 200
    assert call_bg(qtbot, port, "/follow", token=tok, body={"on": True})[0] == 200 and f.enabled
    p.off_air_check.setChecked(False)
    assert not f.enabled
    # слежение включил оператор (пробел) — снятие флажка его не трогает
    p.off_air_check.setChecked(True)
    assert call_bg(qtbot, port, "/follow", token=tok, body={"on": True})[0] == 200 and f.enabled
    f.disable("тест")
    w.toggle_follow()
    assert f.enabled
    p.off_air_check.setChecked(False)
    assert f.enabled
    w.close()


def test_follow_on_undone_if_permission_revoked_meanwhile(qtbot, tmp_path, monkeypatch):
    """Раскачка в потоке слежения сняла разрешение, пока включалось слежение: агент его не оставит."""
    w, port, tok = make_window(qtbot, tmp_path)
    w.agent_panel.allow_check.setChecked(True)
    w.agent_panel.off_air_check.setChecked(True)
    real = w.request_follow

    def racing(on):
        res = real(on)
        w.agent_panel.control_allowed = False  # как revoke() из чужого потока
        return res

    monkeypatch.setattr(w, "request_follow", racing)
    pick = pick_person(qtbot, w)
    assert call_bg(qtbot, port, "/target", token=tok, body=pick)[0] == 200
    code, data = call_bg(qtbot, port, "/follow", token=tok, body={"on": True})
    assert code == 403 and not w.session.follower.enabled
    w.close()


def test_agent_settings_calibrate_and_calibration_lock(qtbot, tmp_path, monkeypatch):
    w, port, tok = make_window(qtbot, tmp_path)
    w.agent_panel.allow_check.setChecked(True)
    w.agent_panel.off_air_check.setChecked(True)
    assert call_bg(qtbot, port, "/composition", token=tok, body={"key": "nope"})[0] == 400
    assert call_bg(qtbot, port, "/composition", token=tok, body={"key": "full"})[0] == 200
    assert call_bg(qtbot, port, "/effort", token=tok, body={"value": 1.5})[0] == 400
    assert call_bg(qtbot, port, "/effort", token=tok, body={"value": 0.8})[0] == 200
    assert w.session.cfg.composition == "full" and w.session.cfg.effort == 0.8
    assert w.follow_panel.effort_slider.value() == 80
    status = json.loads(call(port, "/status", token=tok)[1])
    assert (status["composition"], status["effort"]) == ("full", 0.8)

    # мастер калибровки API не открывает
    code, data = call_bg(qtbot, port, "/calibrate", token=tok, body={})
    assert code == 501 and "в окне" in data["error"]

    # пока идёт калибровка, любое управление — 409
    w.session.follower.polling_paused = True
    for path, body in MOVING + [("/follow", {"on": False})]:
        assert call_bg(qtbot, port, path, token=tok, body=body)[0] == 409, path
    w.session.follower.polling_paused = False

    # открыт модальный диалог (мастер до «Начать», настройки камеры) — тоже 409
    from PySide6.QtWidgets import QApplication

    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: object()))
    for path, body in MOVING:
        code, data = call_bg(qtbot, port, path, token=tok, body=body)
        assert code == 409 and "диалог" in data["error"], path
    monkeypatch.undo()
    w.close()


def test_api_409_while_menu_open(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    w.agent_panel.allow_check.setChecked(True)
    w.agent_panel.off_air_check.setChecked(True)
    w.camera_menu.open()
    for path, body in MOVING:
        code, data = call_bg(qtbot, port, path, token=tok, body=body)
        assert code == 409 and "меню камеры" in data["error"], path
    w.camera_menu.close_menu()
    w.close()


def test_events_frame_and_moment(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.preview.has_frame(), timeout=3000)
    code, raw = call(port, "/frame.jpg", token=tok)
    assert code == 200 and raw[:2] == b"\xff\xd8"
    pick = pick_person(qtbot, w)
    w.session.analyzer.select_at(pick["x"], pick["y"])  # событие target
    qtbot.waitUntil(lambda: any(e["kind"] == "target" for e in w.session.recorder.recent_events(0.0)),
                    timeout=3000)  # фоновый поток записи разбирает очередь
    code, raw = call(port, "/events?since=0", token=tok)
    data = json.loads(raw)
    assert code == 200 and any(e["kind"] == "target" for e in data["events"])
    assert all("image" not in e for e in data["events"])
    later = json.loads(call(port, f"/events?since={data['now']}", token=tok)[1])["events"]
    assert all(e["t"] > data["now"] for e in later)
    code, data = call_bg(qtbot, port, "/moment", token=tok, body={})
    assert code == 200 and data["path"].endswith(".zip")
    w.close()


def test_close_stops_api(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    w.close()
    with pytest.raises(OSError):
        socket.create_connection((HOST, port), timeout=1).close()


# --- несколько камер: флажки и параметр camera ---------------------------------


def analyze_both(w):
    w.cfg.max_analyzed = 2
    w._reallocate_analysis()
    assert all(c.session.follower for c in w.grid.cells[:2])


def allow_cell(w, slot, off_air=True):
    g = w.grid.cells[slot].gate
    g.set_allowed(True)
    g.set_off_air(off_air)


def test_status_lists_cameras(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    allow_cell(w, 1)
    code, raw = call(port, "/status", token=tok)
    data = json.loads(raw)
    assert code == 200 and len(data["cameras"]) == 2
    assert [c["slot"] for c in data["cameras"]] == [1, 2]
    assert [c["camera"]["id"] for c in data["cameras"]] == ["sim1", "sim2"]
    assert data["cameras"][0]["agent"] == {"control_allowed": False, "not_on_air": False}
    assert data["cameras"][1]["agent"] == {"control_allowed": True, "not_on_air": True}
    assert data["camera"]["id"] == "sim1" and data["agent"] == data["cameras"][0]["agent"]
    assert PASSWORD.encode() not in raw and tok.encode() not in raw and b"rtsp_url" not in raw
    w.close()


def test_camera_param_by_slot_and_id(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    analyze_both(w)
    allow_cell(w, 1)
    for q in ("2", "sim2"):
        data = json.loads(call(port, f"/status?camera={q}", token=tok)[1])
        assert data["camera"]["id"] == "sim2" and data["agent"]["control_allowed"], q
    assert json.loads(call(port, "/status?camera=1", token=tok)[1])["camera"]["id"] == "sim1"
    qtbot.waitUntil(lambda: w.cells[1].preview.has_frame(), timeout=3000)
    assert call(port, "/frame.jpg?camera=2", token=tok)[0] == 200
    # в теле POST — тот же параметр
    code, data = call_bg(qtbot, port, "/composition", token=tok, body={"key": "full", "camera": "sim2"})
    assert code == 200 and w.grid.cells[1].cfg.composition == "full"
    assert w.grid.cells[0].cfg.composition != "full"
    w.close()


def test_unknown_camera_404(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    analyze_both(w)
    allow_cell(w, 0)
    allow_cell(w, 1)
    def moves():  # опросы камеры (#G…, #O) идут сами; считаем только команды
        return [[x for x in c.session.sim.received if x.startswith('#') and not x.startswith('#G') and x != '#O'] for c in w.grid.cells[:2]]

    sent = moves()
    for bad in ("nope", "3", "9", "0"):
        code, raw = call(port, f"/status?camera={bad}", token=tok)
        assert code == 404 and "Нет такой камеры в сетке" in json.loads(raw)["error"], bad
        assert call(port, f"/frame.jpg?camera={bad}", token=tok)[0] == 404
        code, data = call_bg(qtbot, port, f"/follow?camera={bad}", token=tok, body={"on": True})
        assert code == 404 and "Нет такой камеры" in data["error"]
        code, data = call_bg(qtbot, port, "/effort", token=tok, body={"value": 0.5, "camera": bad})
        assert code == 404
    assert moves() == sent
    assert not any(c.session.follower.enabled for c in w.grid.cells[:2])
    w.close()


def test_flags_per_camera(qtbot, tmp_path):
    """Флажки у камеры A; оператор двигает камеру B — у A остались; агент на B без флажков — 403."""
    w, port, tok = make_window(qtbot, tmp_path)
    analyze_both(w)
    allow_cell(w, 0)
    w.select_slot(1)  # выбор клетки флажков не трогает
    assert w.grid.cells[0].gate.control_allowed
    allow_cell(w, 1)
    w.controls._on_joystick(0.5, 0.0)  # ручное действие на камере 2
    w.controls.release_all()
    assert not w.grid.cells[1].gate.control_allowed and not w.grid.cells[1].gate.not_on_air
    assert w.grid.cells[0].gate.control_allowed and w.grid.cells[0].gate.not_on_air
    code, data = call_bg(qtbot, port, "/follow?camera=2", token=tok, body={"on": True})
    assert code == 403 and not w.grid.cells[1].session.follower.enabled
    code, data = call_bg(qtbot, port, "/follow", token=tok, body={"on": True, "camera": 2})
    assert code == 403
    # у камеры 1 флажки стоят: её управление доходит дальше проверки разрешений (цели нет — 409)
    code, data = call_bg(qtbot, port, "/follow?camera=sim1", token=tok, body={"on": True})
    assert code == 409 and "цель" in data["error"]
    w.close()


def test_no_param_is_selected(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    analyze_both(w)
    allow_cell(w, 1)
    assert call_bg(qtbot, port, "/effort", token=tok, body={"value": 0.4})[0] == 403  # выбрана камера 1: у неё флажков нет
    w.select_slot(1)
    assert json.loads(call(port, "/status", token=tok)[1])["camera"]["id"] == "sim2"
    assert call_bg(qtbot, port, "/effort", token=tok, body={"value": 0.4})[0] == 200
    assert w.grid.cells[1].cfg.effort == 0.4 and w.grid.cells[0].cfg.effort != 0.4
    w.close()


def test_escape_revokes_all(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    allow_cell(w, 0)
    allow_cell(w, 1)
    w.emergency_stop()
    for c in w.grid.cells[:2]:
        assert not c.gate.control_allowed and not c.gate.not_on_air
    data = json.loads(call(port, "/status", token=tok)[1])
    assert all(not c["agent"]["control_allowed"] for c in data["cameras"])
    assert call_bg(qtbot, port, "/effort?camera=2", token=tok, body={"value": 0.4})[0] == 403
    w.close()


def test_off_air_untick_on_other_camera_stops_only_that_agent_follow(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    analyze_both(w)
    allow_cell(w, 0)
    allow_cell(w, 1)
    c0, c1 = w.grid.cells[0], w.grid.cells[1]
    for c in (c0, c1):
        c.session.follower.polling_paused = True  # без кадров анализа такт сам выключил бы слежение («анализ остановился»)
        c.session.follower.enabled = True
    w._api.backend._agent_followers[c0.gate] = c0.session.follower
    w._api.backend._agent_followers[c1.gate] = c1.session.follower
    c1.gate.set_off_air(False)
    assert c0.session.follower.enabled and not c1.session.follower.enabled, (c0.session.follower.last_reason, c1.session.follower.last_reason)
    w.close()


def test_camera_param_odd_digits_404_and_moment_per_camera(qtbot, tmp_path):
    w, port, tok = make_window(qtbot, tmp_path)
    assert call(port, "/status?camera=%C2%B2", token=tok)[0] == 404  # «²» — не номер клетки
    code, _ = call_bg(qtbot, port, "/moment?camera=zz", token=tok, body={})
    assert code == 404
    code, data = call_bg(qtbot, port, "/moment", token=tok, body={"camera": "2"})
    assert code == 200 and data["path"].endswith(".zip")
    w.close()


def test_menu_opened_from_pad_blocks_agent_on_pad_camera_only(qtbot, tmp_path):
    """Меню Options открыто геймпадом на камере 2, выбрана камера 1: агент не трогает камеру 2, а камеру 1 может."""
    w, port, tok = make_window(qtbot, tmp_path)
    analyze_both(w)
    allow_cell(w, 0)
    allow_cell(w, 1)
    pad = w.pad_connected()
    w.pad_create(pad)
    assert w.pad_router.slot_of(pad) == 1 and w.grid.selected == 0
    w._menu_pad = pad
    w.camera_menu.open()
    assert w.camera_menu.is_open
    code, data = call_bg(qtbot, port, "/composition?camera=2", token=tok, body={"key": "full"})
    assert code == 409 and "меню" in data["error"]
    code, _ = call_bg(qtbot, port, "/composition?camera=1", token=tok, body={"key": "full"})
    assert code == 200
    w.camera_menu.close_menu()
    w.close()


def test_start_does_not_ask_host_name(monkeypatch):
    # http.server спрашивает у системы имя хоста; без обратного DNS это до 35 с — окно ждало бы API
    def slow_lookup(*a):
        raise AssertionError("getfqdn при старте сервера")

    monkeypatch.setattr(socket, "getfqdn", slow_lookup)
    srv = ApiServer(FakeBackend(), "tok-123", 0)
    srv.start()
    try:
        assert call(srv.port, "/status", token="tok-123")[0] == 200
    finally:
        srv.stop()
