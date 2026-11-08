"""Контроллер записи: старт и стоп, запас места, сводка для нижней строки."""

from pathlib import Path
from types import SimpleNamespace

from ptztracker.config import AppConfig
from ptztracker.videorec import controller as K
from ptztracker.videorec.controller import VideoController
from ptztracker.videorec.disk import RATE_CAMERA_BPS, DiskInfo
from ptztracker.videorec.recorder import IDLE, RecState
from ptztracker.videorec.writer import FAT32_SPLIT_BYTES


class FakeDisk:
    def __init__(self, info):
        self.info = info

    def snapshot(self):
        return self.info

    def start(self):
        pass

    def stop(self):
        pass

    def refresh(self):
        pass


class FakeSession:
    def __init__(self, cid="c1", name="Сцена"):
        self.cfg = SimpleNamespace(id=cid, name=name)
        self.state = IDLE
        self.started = []
        self.notes = []

    def video_state(self):
        return self.state

    def start_video(self, folder, mode, split_bytes):
        self.started.append((folder, mode, split_bytes))
        self.state = RecState(active=True)

    def stop_video(self):
        self.state = IDLE

    def video_note(self):
        return self.notes.pop(0) if self.notes else None


GB = 1_000_000_000


def disk(free=412 * GB, label="E:", fs="exfat", connected=True):
    return DiskInfo(connected, free, 1000 * GB, label, "T7", True, fs)


def make(sessions, info=None, **cfg):
    return VideoController(AppConfig(**cfg), lambda: list(enumerate(sessions)), FakeDisk(info or disk()))


def active(elapsed=754.0, size=int(2.4 * GB), rate=0.0, signal=True):
    return RecState(active=True, signal=signal, elapsed_s=elapsed, bytes_total=size, rate_bps=rate)


def test_toggle_starts_and_stops():
    s = FakeSession()
    c = make([s], video_dir="/tmp/v", video_mode="light")
    assert c.toggle(s) is None
    assert s.started == [(Path("/tmp/v"), "light", None)] and c.any_active()
    assert c.toggle(s) is None
    assert not c.any_active() and len(s.started) == 1


def test_double_toggle_leaves_nothing_running():
    s = FakeSession()
    c = make([s])
    c.toggle(s)
    c.toggle(s)
    assert s.state.active is False and len(s.started) == 1


def test_disconnected_disk_refuses_with_reason():
    s = FakeSession()
    c = make([s], disk(connected=False, free=0))
    msg = c.toggle(s)
    assert "не подключён" in msg and "E:" in msg and s.started == []


def test_fat32_splits_files():
    s = FakeSession()
    make([s], disk(fs="FAT32")).toggle(s)
    assert s.started[0][2] == FAT32_SPLIT_BYTES


def test_exfat_does_not_split():
    s = FakeSession()
    make([s], disk(fs="exfat")).toggle(s)
    assert s.started[0][2] is None


def test_summary_text():
    a, b, gap = FakeSession("a"), FakeSession("b"), FakeSession("g")
    c = make([a, gap, b])
    a.state, b.state = active(rate=0.0), active(rate=0.0)
    summary, msgs = c.tick()
    # 412 ГБ при двух камерах по 3,19 МБ/с — около 17,9 ч
    assert summary.text == "Запись: 1, 3 · 12:34 · 4,8 ГБ · на E: свободно 412 ГБ (~17 ч)"
    assert [r.slot for r in summary.rows] == [0, 2] and summary.level == "" and msgs == []


def test_empty_summary_when_nothing_records():
    summary, _ = make([FakeSession()]).tick()
    assert summary.rows == [] and summary.text == ""


def _minutes(m, n=1):
    return int(m * 60 * RATE_CAMERA_BPS * n)


def test_levels_by_minutes_left():
    for minutes, level in ((25, ""), (15, "warn"), (4, "crit")):
        s = FakeSession()
        c = make([s], disk(free=_minutes(minutes)))
        s.state = active()
        assert c.tick()[0].level == level


def test_autostop_below_one_minute_says_once():
    s = FakeSession()
    c = make([s], disk(free=_minutes(0.5)))
    s.state = active()
    _, msgs = c.tick()
    assert not s.state.active and len(msgs) == 1 and "мест" in msgs[0]
    assert c.tick()[1] == []


def test_camera_without_signal_not_counted_in_rate():
    a, b = FakeSession("a"), FakeSession("b")
    c = make([a, b], disk(free=_minutes(15)))
    a.state, b.state = active(), active(signal=False)
    assert c.tick()[0].level == "warn"  # 15 минут на одну камеру, а не 7,5 на две


def test_real_rate_replaces_estimate():
    s = FakeSession()
    c = make([s], disk(free=10 * GB))
    s.state = active(rate=25e6)  # 400 секунд
    assert "(~6 мин)" in c.tick()[0].text


def test_vanished_recording_is_reported_once():
    s = FakeSession()
    sessions = [s]
    c = VideoController(AppConfig(), lambda: list(enumerate(sessions)), FakeDisk(disk()))
    c.toggle(s)
    s.state = active()
    c.tick()
    sessions.clear()
    _, msgs = c.tick()
    assert len(msgs) == 1 and "Сцена" in msgs[0]
    assert c.tick()[1] == []


def test_recorder_error_and_notes_are_reported_once():
    s = FakeSession()
    c = make([s])
    c.toggle(s)
    s.state = active()
    s.notes.append("нет сигнала")
    assert c.tick()[1] == ["нет сигнала"]
    s.state = RecState(active=False, error="диск")
    assert c.tick()[1] == ["диск"] and c.tick()[1] == []


def test_user_stop_is_silent():
    s = FakeSession()
    c = make([s])
    c.toggle(s)
    s.state = active()
    c.tick()
    c.toggle(s)
    assert c.tick()[1] == []


def test_watches_folder_of_running_recording(tmp_path):
    s = FakeSession()
    cfg = AppConfig(video_dir=str(tmp_path / "a"))
    c = VideoController(cfg, lambda: [(0, s)], FakeDisk(disk()))
    c.toggle(s)
    cfg.video_dir = str(tmp_path / "b")
    assert c.current_folder() == tmp_path / "a"
    c.toggle(s)
    assert c.current_folder() == tmp_path / "b"


def test_stop_all(monkeypatch):
    a, b = FakeSession("a"), FakeSession("b")
    c = make([a, b])
    c.toggle(a)
    c.toggle(b)
    c.stop_all()
    assert not c.any_active() and K.STOP_MIN == 1
