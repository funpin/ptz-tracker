"""Рекордер одной камеры: кадры симулятора, RTSP, страховка, обрыв связи, ошибки диска."""

import threading
import time

import av
import numpy as np
import pytest
from video_helpers import count_frames

from ptztracker.video.frames import Frame
from ptztracker.videorec import recorder as R
from ptztracker.videorec.recorder import VideoRecorder


def wait_for(cond, t=5.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class FakeFrames:
    """Источник кадров с растущим номером, как у SimFrameSource."""

    def __init__(self, empty=False):
        self.seq, self.empty = 0, empty
        self.img = np.zeros((240, 320, 3), np.uint8)

    def latest(self):
        if self.empty:
            return None
        self.seq += 1
        time.sleep(0.005)
        return Frame(self.img, time.monotonic(), self.seq)


def frames_rec(folder, **kw):
    return VideoRecorder("Сцена", folder, frames=kw.pop("frames", FakeFrames()), **kw)


def test_records_frames_to_readable_file(tmp_path):
    r = frames_rec(tmp_path)
    r.start()
    assert r.snapshot().active
    assert wait_for(lambda: r.snapshot().bytes_total > 0)
    r.stop()
    assert r.wait_closed(5.0)
    s = r.snapshot()
    assert not s.active and s.error is None and len(s.paths) == 1 and count_frames(s.paths[0]) > 0
    assert s.paths[0].name.startswith("Сцена_")


def test_stop_does_not_wait_for_the_thread(tmp_path):
    r = frames_rec(tmp_path)
    r.start()
    wait_for(lambda: r.snapshot().bytes_total > 0)
    t0 = time.monotonic()
    r.stop()
    assert time.monotonic() - t0 < 0.05 and not r.snapshot().active
    r.wait_closed(5.0)


def test_folder_that_cannot_be_created_reports_error(tmp_path):
    (tmp_path / "x").write_text("обычный файл")
    r = frames_rec(tmp_path / "x" / "записи")
    r.start()
    assert wait_for(lambda: not r.snapshot().active)
    s = r.snapshot()
    assert "папк" in s.error and s.paths == ()


def test_stop_before_first_frame_leaves_no_file(tmp_path):
    r = frames_rec(tmp_path, frames=FakeFrames(empty=True))
    r.start()
    r.stop()
    assert r.wait_closed(5.0) and list(tmp_path.iterdir()) == []


def test_disk_write_error_stops_only_this_recorder(tmp_path, monkeypatch):
    real = R.EncodeWriter
    calls = {"n": 0}

    class Failing(real):
        def write_frame(self, *a):
            calls["n"] += 1
            if self.stem.startswith("Плохая") and calls["n"] > 3:
                raise OSError(28, "No space left on device")
            super().write_frame(*a)

    monkeypatch.setattr(R, "EncodeWriter", Failing)
    bad = VideoRecorder("Плохая", tmp_path, frames=FakeFrames())
    good = VideoRecorder("Хорошая", tmp_path, frames=FakeFrames())
    bad.start()
    good.start()
    assert wait_for(lambda: not bad.snapshot().active)
    assert bad.snapshot().error and good.snapshot().active
    good.stop()
    good.wait_closed(5.0)


def test_folder_removed_while_recording_stops_with_error(tmp_path):
    import shutil

    folder = tmp_path / "диск"
    r = frames_rec(folder)
    r.start()
    assert wait_for(lambda: r.snapshot().bytes_total > 0)
    shutil.rmtree(folder)
    assert wait_for(lambda: not r.snapshot().active)
    assert "папк" in r.snapshot().error


def finish_when_written(r):
    wait_for(lambda: r.snapshot().bytes_total > 0)
    r.stop()
    r.wait_closed(5.0)


def test_light_mode_uses_bitrate_and_camera_mode_does_not(tmp_path, monkeypatch):
    seen = []
    real = R.EncodeWriter

    class Spy(real):
        def __init__(self, *a, **k):
            seen.append((k.get("bit_rate"), k.get("max_rate")))
            super().__init__(*a, **k)

    monkeypatch.setattr(R, "EncodeWriter", Spy)
    for mode in ("camera", "light"):
        r = frames_rec(tmp_path / mode, mode=mode)
        r.start()
        finish_when_written(r)
    assert seen == [(None, None), (3_000_000, 6_000_000)]


# --- RTSP (настоящая камера с Mac недоступна: источник — локальный файл с настоящим H.264) ---


@pytest.fixture
def fast_retry(monkeypatch):
    monkeypatch.setattr(R, "RETRY_DELAYS_S", (0.02, 0.05, 0.1))


class Inputs:
    """open_input: по очереди отдаёт контейнеры или бросает ошибки; дальше — отказ."""

    def __init__(self, *items):
        self.items, self.calls = list(items), []

    def __call__(self, url):
        self.calls.append(time.monotonic())
        item = self.items.pop(0) if self.items else ConnectionRefusedError("нет")
        if isinstance(item, Exception):
            raise item
        return item() if callable(item) else av.open(str(item))


def rtsp_rec(folder, inputs, **kw):
    return VideoRecorder("Сцена", folder, url=lambda: "rtsp://u:secret@h/x", open_input=inputs, **kw)


def finish(r):
    r.stop()
    assert r.wait_closed(5.0)
    return r.snapshot()


def test_copy_keeps_every_frame_without_decoding(tmp_path, h264_file, fast_retry, monkeypatch):
    def no_decode(*a, **k):
        raise AssertionError("в копии нельзя декодировать")

    monkeypatch.setattr(av.Packet, "decode", no_decode, raising=False)
    r = rtsp_rec(tmp_path, Inputs(h264_file))
    r.start()
    assert wait_for(lambda: not r.snapshot().signal and r.snapshot().bytes_total > 0)
    s = finish(r)
    monkeypatch.undo()  # подсчёт кадров в проверке декодирует сам
    assert count_frames(s.paths[0]) == 75


def test_light_mode_is_25_fps_from_50(tmp_path, h264_file_50, fast_retry):
    r = rtsp_rec(tmp_path, Inputs(h264_file_50), mode="light")
    r.start()
    assert wait_for(lambda: not r.snapshot().signal and r.snapshot().bytes_total > 0)
    s = finish(r)
    assert abs(count_frames(s.paths[0]) - 50) <= 2


class BreaksMidway:
    """Контейнер, у которого поток обрывается посреди чтения."""

    def __init__(self, path, after):
        self.c, self.after = av.open(str(path)), after
        self.streams = self.c.streams

    def demux(self, stream):
        for i, p in enumerate(self.c.demux(stream)):
            if i == self.after:
                raise av.error.ConnectionResetError(104, "обрыв")
            yield p

    def close(self):
        self.c.close()


def test_connection_loss_continues_in_new_file(tmp_path, h264_file, fast_retry):
    inputs = Inputs(lambda: BreaksMidway(h264_file, 20), ConnectionRefusedError("нет"), h264_file)
    r = rtsp_rec(tmp_path, inputs)
    r.start()
    assert wait_for(lambda: len(r.snapshot().paths) == 2 and not r.snapshot().signal)
    s = r.snapshot()
    assert s.active and [p.name.split("_")[-1] for p in s.paths][1] == "02.mp4"
    assert r.take_note() is not None
    finish(r)


def test_one_note_per_outage(tmp_path, h264_file, fast_retry):
    r = rtsp_rec(tmp_path, Inputs(h264_file))
    r.start()
    assert wait_for(lambda: not r.snapshot().signal and r.snapshot().bytes_total > 0)
    time.sleep(0.25)  # несколько неудачных подключений подряд
    assert r.take_note() is not None and r.take_note() is None
    finish(r)


def test_refused_connections_back_off(tmp_path, h264_file, fast_retry):
    no = ConnectionRefusedError("нет")
    inputs = Inputs(no, no, no, h264_file)
    r = rtsp_rec(tmp_path, inputs)
    r.start()
    assert wait_for(lambda: len(inputs.calls) >= 4 and r.snapshot().bytes_total > 0)
    gaps = [b - a for a, b in zip(inputs.calls, inputs.calls[1:], strict=False)]
    assert gaps[0] >= 0.02 and gaps[1] >= 0.05 and gaps[2] >= 0.1
    finish(r)


def test_stream_that_cannot_be_copied_falls_back_to_encoding(tmp_path, h264_file, fast_retry, monkeypatch):
    real = R.CopyWriter

    class Refuses(real):
        def write_packet(self, packet):
            (self.folder).mkdir(parents=True, exist_ok=True)
            p = self.folder / "пусто.mp4"
            p.touch()
            self.paths.append(p)
            raise av.error.InvalidDataError(1, "нельзя")

    monkeypatch.setattr(R, "CopyWriter", Refuses)
    r = rtsp_rec(tmp_path, Inputs(h264_file))
    r.start()
    assert wait_for(lambda: not r.snapshot().signal and r.snapshot().bytes_total > 0)
    assert r.snapshot().degraded and "лёгк" not in (r.take_note() or "").lower()
    s = finish(r)
    assert s.error is None and count_frames(s.paths[0]) > 0
    assert not (tmp_path / "пусто.mp4").exists()


def test_disk_error_is_not_mistaken_for_bad_stream(tmp_path, h264_file, fast_retry, monkeypatch):
    real = R.CopyWriter

    class Full(real):
        def write_packet(self, packet):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(R, "CopyWriter", Full)
    r = rtsp_rec(tmp_path, Inputs(h264_file))
    r.start()
    assert wait_for(lambda: not r.snapshot().active)
    assert r.snapshot().error and r.take_note() is None


def test_password_never_reaches_messages(tmp_path, fast_retry):
    r = rtsp_rec(tmp_path, Inputs(OSError("rtsp://u:secret@h/x отказ")))
    r.start()
    assert wait_for(lambda: not r.snapshot().signal)
    note = r.take_note()
    s = finish(r)
    assert "secret" not in (note or "") and "secret" not in (s.error or "")


def test_url_is_built_in_recorder_thread(tmp_path, h264_file, fast_retry):
    seen = []

    def url():
        seen.append(threading.current_thread() is threading.main_thread())
        return "rtsp://h/x"

    r = VideoRecorder("Сцена", tmp_path, url=url, open_input=Inputs(h264_file))
    r.start()
    assert wait_for(lambda: bool(seen))
    finish(r)
    assert seen[0] is False


def test_unwritable_folder_is_reported_even_without_camera(tmp_path, fast_retry):
    (tmp_path / "x").write_text("обычный файл")
    r = rtsp_rec(tmp_path / "x" / "записи", Inputs())
    r.start()
    assert wait_for(lambda: not r.snapshot().active, t=2.0)
    assert "папк" in r.snapshot().error


def test_probe_leaves_folder_empty(tmp_path):
    r = frames_rec(tmp_path, frames=FakeFrames(empty=True))
    r.start()
    time.sleep(0.1)
    r.stop()
    assert r.wait_closed(5.0) and list(tmp_path.iterdir()) == []


class FlakyMux:
    """Настоящий CopyWriter, у которого отдельные пакеты не проходят mux (сбой отметок времени)."""

    def __init__(self, bad):
        self.bad, self.n = bad, 0


def _flaky(monkeypatch, bad):
    real = R.CopyWriter
    state = FlakyMux(bad)

    class Flaky(real):
        def write_packet(self, packet):
            state.n += 1
            if state.n in state.bad:
                raise av.error.ArgumentError(22, "non monotonic dts")
            super().write_packet(packet)

    monkeypatch.setattr(R, "CopyWriter", Flaky)


def test_single_bad_packet_keeps_copying(tmp_path, h264_file, fast_retry, monkeypatch):
    _flaky(monkeypatch, {10})
    r = rtsp_rec(tmp_path, Inputs(h264_file))
    r.start()
    assert wait_for(lambda: not r.snapshot().signal and r.snapshot().bytes_total > 0)
    note = r.take_note()
    s = finish(r)
    assert "лёгк" not in (note or "").lower() and r._mode == "camera"
    assert count_frames(s.paths[0]) >= 70


def test_long_run_of_bad_packets_falls_back(tmp_path, h264_file, fast_retry, monkeypatch):
    _flaky(monkeypatch, set(range(5, 40)))
    r = rtsp_rec(tmp_path, Inputs(h264_file))
    r.start()
    assert wait_for(lambda: r.snapshot().degraded)
    finish(r)
