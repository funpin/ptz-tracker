"""Геймпад в окне: источники скорости, стопы, слежение, вибрация, полоса (симулятор + фальшивое устройство)."""

import threading

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

from ptztracker.config import ConfigStore
from ptztracker.gamepad.device import RawPad
from ptztracker.gamepad.feedback import (
    RUMBLE_FOLLOW_AUTO_OFF,
    RUMBLE_FOLLOW_ON,
    RUMBLE_LIMIT,
    RUMBLE_NOWHERE,
    RUMBLE_PRESET_STORED,
    PadStatus,
)
from ptztracker.ui.main_window import MainWindow


class PadBackend:
    """Фальшивый DualSense: тест кладёт состояние в .pad (None — отключён)."""

    def __init__(self, fail=False):
        self.pad = RawPad()
        self.fail = fail
        self.batt = (None, None)
        self.calls = []
        self._lock = threading.Lock()

    def open(self):
        if self.fail:
            raise RuntimeError("SDL не запустился")
        return True

    def poll(self):
        return self.pad

    def info(self):
        return {"name": "DualSense", "type": "ps5", "has_led": True, "has_rumble": True, "power": None}

    def _rec(self, *c):
        with self._lock:
            self.calls.append(c)

    def set_led(self, rgb):
        self._rec("led", tuple(rgb))

    def set_player_index(self, n):
        self._rec("player", n)

    def rumble(self, low, high, ms):
        self._rec("rumble", low, high, ms)

    def battery(self):
        return self.batt

    def close(self):
        pass

    def of(self, kind):
        with self._lock:
            return [c[1:] for c in self.calls if c[0] == kind]


def stick(x=0.0, buttons=(), **axes):
    return RawPad({"leftx": x, **axes}, frozenset(buttons))


def make(qtbot, tmp_path, backend=None, cameras=None):
    b = backend or PadBackend()
    store = ConfigStore(tmp_path / "c.json")
    if cameras is not None:
        cfg = store.load()
        cfg.cameras = cameras
        store.save(cfg)
    w = MainWindow(store, use_sim=True, gamepad_backend=b)
    qtbot.addWidget(w)
    w.show()
    if not b.fail:
        qtbot.waitUntil(lambda: w.pad.connected, timeout=2000)
    return w, b


def pts(sim, start=0):
    return [c for c in list(sim.received)[start:] if c.startswith("#PTS")]


def moving_right(cmd: str) -> bool:
    return cmd.startswith("#PTS") and int(cmd[4:6]) > 50 and int(cmd[6:8]) == 50


def select(qtbot, w):
    a = w.session.analyzer
    qtbot.waitUntil(lambda: bool(a.latest() and a.latest().tracks), timeout=8000)
    tr = a.latest().tracks[0]
    assert a.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2) is not None


def allow_agent(w):
    w.agent_panel.allow_check.setChecked(True)
    w.agent_panel.off_air_check.setChecked(True)


def wait_right(qtbot, sim, start=0):
    qtbot.waitUntil(lambda: bool(pts(sim, start)) and moving_right(pts(sim, start)[-1]), timeout=2000)


def test_stick_pans_and_disables_follow_before_move(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    select(qtbot, w)
    f = w.session.follower
    assert f.enable()
    allow_agent(w)
    sim = w.session.sim
    seen = []
    sched = w.session.scheduler
    sub = sched.submit

    def spy(key, cmd, *a, **kw):
        if key == "pt" and moving_right(cmd):
            seen.append((f.enabled, w.agent_panel.control_allowed))
        return sub(key, cmd, *a, **kw)

    sched.submit = spy
    n = len(list(sim.received))
    b.pad = stick(1.0)
    wait_right(qtbot, sim, n)
    assert not f.enabled and not w.agent_panel.control_allowed
    assert seen[0] == (False, False)  # первая команда движения: слежение и агент уже сняты
    after = pts(sim, n)
    first_right = next(i for i, c in enumerate(after) if moving_right(c))
    assert all(moving_right(c) for c in after[first_right:])  # после ручного движения слежение не вмешивается
    w.close()


def test_deactivate_keeps_gamepad_motion(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    sim = w.session.sim
    b.pad = stick(1.0)
    wait_right(qtbot, sim)
    w.isActiveWindow = lambda: False
    QApplication.sendEvent(w, QEvent(QEvent.ActivationChange))
    qtbot.wait(400)
    assert pts(sim)[-1] != "#PTS5050"
    assert moving_right(pts(sim)[-1])
    w.close()


def test_keyboard_release_keeps_gamepad(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    sim = w.session.sim
    b.pad = stick(1.0)
    wait_right(qtbot, sim)
    qtbot.keyPress(w, Qt.Key_Up)
    qtbot.wait(300)
    qtbot.keyRelease(w, Qt.Key_Up)
    qtbot.wait(400)
    assert moving_right(pts(sim)[-1])
    w.close()


def test_keyboard_and_pad_sum_clamped(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    sent = []
    sub = w.session.set_pan_tilt_velocity
    w.session.set_pan_tilt_velocity = lambda vx, vy: (sent.append((vx, vy)), sub(vx, vy))
    b.pad = stick(1.0)
    qtbot.waitUntil(lambda: bool(sent) and sent[-1][0] == 1.0, timeout=2000)
    qtbot.keyPress(w, Qt.Key_Right)
    assert sent[-1] == (1.0, 0.0)  # 1,0 + 0,4 → не больше 1
    qtbot.keyPress(w, Qt.Key_Up)
    assert sent[-1] == (1.0, 0.4)
    qtbot.keyRelease(w, Qt.Key_Up)
    qtbot.keyRelease(w, Qt.Key_Right)
    w.close()


def test_disconnect_stops_motion_keeps_follow(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    select(qtbot, w)
    f = w.session.follower
    assert f.enable()
    sent = []
    sub = w.session.set_pan_tilt_velocity
    w.session.set_pan_tilt_velocity = lambda vx, vy: (sent.append((vx, vy)), sub(vx, vy))
    b.pad = None  # стик в центре: камере геймпад ничего не шлёт
    qtbot.waitUntil(lambda: not w.pad.connected, timeout=2000)
    qtbot.wait(200)
    assert f.enabled
    assert sent == []
    # со стиком вправо при выключенном слежении — отключение останавливает камеру
    f.disable("выключено оператором")
    b.pad = RawPad()
    qtbot.waitUntil(lambda: w.pad.connected, timeout=2000)
    qtbot.wait(100)
    sim = w.session.sim
    n = len(list(sim.received))
    b.pad = stick(1.0)
    wait_right(qtbot, sim, n)
    b.pad = None
    qtbot.waitUntil(lambda: pts(sim)[-1] == "#PTS5050", timeout=2000)
    assert w.pad.velocity() == (0.0, 0.0, 0.0)
    w.close()


def test_touchpad_stop_latches(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    sim = w.session.sim
    b.pad = stick(1.0)
    wait_right(qtbot, sim)
    b.pad = stick(1.0, buttons={"touchpad"})
    qtbot.waitUntil(lambda: pts(sim)[-1] == "#PTS5050", timeout=2000)
    b.pad = stick(0.9)
    qtbot.wait(400)
    n = len(list(sim.received))
    qtbot.wait(300)
    assert not any(moving_right(c) for c in pts(sim, n))
    assert pts(sim)[-1] == "#PTS5050"
    b.pad = stick(0.0)
    qtbot.wait(100)
    b.pad = stick(0.8)
    wait_right(qtbot, sim, n)
    w.close()


def test_camera_switch_requires_recenter(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    old = w.session.sim
    b.pad = stick(1.0)
    wait_right(qtbot, old)
    w.set_cell_camera(w.grid.selected, w._current_cfg().id)
    assert pts(old)[-1] == "#PTS5050"
    sim = w.session.sim
    b.pad = stick(0.95)  # стик всё ещё держат — новая камера не едет
    qtbot.wait(500)
    assert not any(moving_right(c) for c in pts(sim))
    b.pad = stick(0.0)
    qtbot.wait(100)
    # стик в центре, слежение на новой камере, геймпад выдернули — новой камере ничего не шлём
    select(qtbot, w)
    f = w.session.follower
    assert f.enable()
    sent = []
    sub = w.session.set_pan_tilt_velocity
    w.session.set_pan_tilt_velocity = lambda vx, vy: (sent.append((vx, vy)), sub(vx, vy))
    b.pad = None
    qtbot.waitUntil(lambda: not w.pad.connected, timeout=2000)
    qtbot.wait(200)
    assert sent == [] and f.enabled
    f.disable("выключено оператором")
    b.pad = RawPad()
    qtbot.waitUntil(lambda: w.pad.connected, timeout=2000)
    qtbot.wait(100)
    b.pad = stick(0.8)
    wait_right(qtbot, sim)
    w.close()


def press(qtbot, b, button, hold_s=0.05):
    b.pad = stick(buttons={button})
    qtbot.wait(int(hold_s * 1000))
    b.pad = stick()
    qtbot.wait(80)


def test_r3_toggles_like_space(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    select(qtbot, w)
    f = w.session.follower
    press(qtbot, b, "r3")
    qtbot.waitUntil(lambda: f.enabled, timeout=1000)
    allow_agent(w)
    press(qtbot, b, "r3")
    qtbot.waitUntil(lambda: not f.enabled, timeout=1000)
    assert f.last_reason == "выключено оператором"
    assert not w.agent_panel.control_allowed  # как Пробел: оператор забрал камеру
    w.close()


def test_l1_r1_composition_steps(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    cam = w._current_cfg()
    w._on_composition("waist_center")  # SIM_CAMERA общая для всех тестов: крупность мог поменять предыдущий

    def step(button, want):
        press(qtbot, b, button)
        qtbot.waitUntil(lambda: cam.composition == want, timeout=1000)

    step("l1", "full")  # шире: пояс → рост → общий
    step("l1", "wide")
    press(qtbot, b, "l1")  # шире некуда — короткий импульс
    qtbot.waitUntil(lambda: b.of("rumble") == [RUMBLE_NOWHERE[0]], timeout=1000)
    assert cam.composition == "wide"
    step("r1", "full")
    step("r1", "waist_center")
    w._on_composition("waist_left")  # оператор выбрал сторону клавишей
    step("l1", "full")
    step("r1", "waist_left")  # в пояс — на последнюю сторону
    press(qtbot, b, "r1")  # ближе пояса некуда
    qtbot.waitUntil(lambda: len(b.of("rumble")) == 2, timeout=1000)
    assert cam.composition == "waist_left"
    w._on_composition("waist_center")
    w.close()


def test_nowhere_neighbor_rumbles(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w.session.analyzer.select_neighbor = lambda d: None
    b.pad = stick(rightx=1.0)
    qtbot.waitUntil(lambda: b.of("rumble") == [RUMBLE_NOWHERE[0]], timeout=1000)
    w.close()


def test_iris_step_refused_rumbles(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w.session.iris_step = lambda d: False
    press(qtbot, b, "dpad_right")
    qtbot.waitUntil(lambda: b.of("rumble") == [RUMBLE_NOWHERE[0]], timeout=1000)
    w.close()


def test_preset_store_rumbles(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    sim = w.session.sim
    b.pad = stick(buttons={"square"})
    qtbot.waitUntil(lambda: any(c.startswith("#M") for c in sim.received), timeout=3000)
    qtbot.waitUntil(lambda: b.of("rumble") == [RUMBLE_PRESET_STORED[0]], timeout=1000)
    assert "#M02" in sim.received
    b.pad = stick()
    qtbot.wait(150)
    assert not any(c.startswith("#R") for c in sim.received)  # после записи отпускание ничего не вызывает
    press(qtbot, b, "cross")
    qtbot.waitUntil(lambda: "#R00" in sim.received, timeout=1000)
    w.close()


def test_follow_on_double_pulse(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    select(qtbot, w)
    qtbot.keyClick(w, Qt.Key_Space)
    assert w.session.follower.enabled
    on = [s for s in RUMBLE_FOLLOW_ON if s[0]]
    qtbot.waitUntil(lambda: b.of("rumble") == on, timeout=2000)
    w.close()


def test_auto_off_long_pulse_not_for_operator(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    select(qtbot, w)
    f = w.session.follower
    assert f.enable()
    qtbot.wait(500)
    qtbot.keyClick(w, Qt.Key_Space)  # оператор выключил
    assert not f.enabled
    qtbot.wait(400)
    assert RUMBLE_FOLLOW_AUTO_OFF[0] not in b.of("rumble")
    assert f.enable()
    qtbot.wait(500)
    t = threading.Thread(target=f.disable, args=("анализ остановился",))
    t.start()
    t.join()
    qtbot.waitUntil(lambda: RUMBLE_FOLLOW_AUTO_OFF[0] in b.of("rumble"), timeout=1000)
    w.close()


def test_limit_pulse_only_while_pad_pushes(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    lw = w.session.limit_watch
    t = threading.Thread(target=lw._on_limit)  # как из потока LimitWatch
    t.start()
    t.join()
    qtbot.wait(150)
    assert b.of("rumble") == []  # геймпад не давит — не вибрируем
    b.pad = stick(1.0)
    qtbot.waitUntil(lambda: w.pad.velocity()[0] == 1.0, timeout=1000)
    t = threading.Thread(target=lw._on_limit)
    t.start()
    t.join()
    qtbot.waitUntil(lambda: b.of("rumble") == [RUMBLE_LIMIT[0]], timeout=1000)
    w.close()


def test_lightbar_changes_only_on_change(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w._follow_timer.stop()
    qtbot.wait(100)
    before = len(b.of("led"))
    w.pad._led = None
    w._sync_pad()
    w._sync_pad()
    qtbot.wait(100)
    assert len(b.of("led")) == before + 1
    w.close()


def test_lightbar_priority_from_window(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w._follow_timer.stop()
    got = []
    w.pad.set_status = lambda s, t=None: got.append(s)
    w._sync_pad()
    assert got[-1] == PadStatus(on_program=False, on_preview=False, link_ok=w.session.online, standby=False,
                                menu_open=False, following=False)
    w.close()


def test_no_pygame_window_works(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path, backend=PadBackend(fail=True))
    assert not w.pad.available and not w.pad.connected
    sim = w.session.sim
    qtbot.keyPress(w, Qt.Key_Right)
    qtbot.wait(400)
    qtbot.keyRelease(w, Qt.Key_Right)
    qtbot.wait(300)
    assert sim.pan_deg > 0.5
    assert pts(sim)[-1] == "#PTS5050"
    w._sync_pad()
    w.emergency_stop()
    w.close()


def test_player_lights_follow_cell_number(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    # огоньки = номер клетки камеры геймпада (до подключения мост может прислать «погасить» — ждём последнее)
    qtbot.waitUntil(lambda: bool(b.of("player")) and b.of("player")[-1] == (1,), timeout=1000)
    w.select_slot(1)  # выбор клетки геймпад не переводит
    qtbot.wait(150)
    assert b.of("player")[-1] == (1,)
    w.pad_create(w._bridge_pad)
    qtbot.waitUntil(lambda: b.of("player")[-1] == (2,), timeout=1000)
    w.close()


def test_create_short_moves_pad_to_next_camera_wraps(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)  # в --sim открыты клетки 1 и 2
    assert w.pad_router.slot_of(w._bridge_pad) == 0
    press(qtbot, b, "create")
    qtbot.waitUntil(lambda: w.pad_router.slot_of(w._bridge_pad) == 1, timeout=2000)
    press(qtbot, b, "create")
    qtbot.waitUntil(lambda: w.pad_router.slot_of(w._bridge_pad) == 0, timeout=2000)
    assert w.grid.selected == 0  # выбранная клетка (клавиатура) не менялась
    w.close()


def test_create_single_camera_nowhere(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    w.set_cell_camera(1, None)  # остаётся одна открытая клетка
    session = w.session
    press(qtbot, b, "create")
    qtbot.waitUntil(lambda: b.of("rumble") == [RUMBLE_NOWHERE[0]], timeout=1000)
    assert w.session is session
    w.close()


def test_create_long_saves_moment(qtbot, tmp_path):
    w, b = make(qtbot, tmp_path)
    reasons = []
    w.save_moment = lambda reason="F9": reasons.append(reason)
    b.pad = stick(buttons={"create"})
    qtbot.waitUntil(lambda: reasons == ["геймпад"], timeout=3000)
    qtbot.waitUntil(lambda: b.of("rumble") == [RUMBLE_PRESET_STORED[0]], timeout=1000)
    b.pad = stick()
    qtbot.wait(200)
    assert reasons == ["геймпад"] and b.of("rumble") == [RUMBLE_PRESET_STORED[0]]
    w.close()


def test_battery_exposed_on_bridge(qtbot, tmp_path):
    b = PadBackend()
    b.batt = (80, False)
    w, b = make(qtbot, tmp_path, backend=b)
    qtbot.waitUntil(lambda: w.pad.battery_percent == 80, timeout=2000)
    assert w.pad.battery_charging is False and w.pad.battery_remaining_s() is None
    w.pad._on_batt(70, True)
    assert w.pad.battery_charging is True and w.pad.battery_remaining_s() is None
    w.pad._on_conn(None)
    assert w.pad.battery_percent is None and w.pad.battery_charging is None
    w.close()
