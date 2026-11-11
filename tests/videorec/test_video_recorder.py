"""Рекордер одной камеры: кадры симулятора, RTSP, страховка, обрыв связи, ошибки диска."""

import sys
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
    (tmp_path / "x").write_text("обычный файл", encoding="utf-8")
    r = frames_rec(tmp_path / "x" / "записи")
    r.start()
    assert wait_for(lambda: not r.snapshot().active)
    s = r.snapshot()
    assert "диск" in s.error and s.paths == ()


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


@pytest.mark.skipif(sys.platform == "win32", reason="Windows не даёт удалить папку с открытым файлом")
def test_folder_removed_while_recording_stops_with_error(tmp_path):
    import shutil

    folder = tmp_path / "диск"
    r = frames_rec(folder)
    r.start()
    assert wait_for(lambda: r.snapshot().bytes_total > 0)
    shutil.rmtree(folder)
    assert wait_for(lambda: not r.snapshot().active)
    assert "диск" in r.snapshot().error


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
    # часы Windows идут шагами ~16 мс: пауза может выглядеть чуть короче заданной
    assert all(gap >= delay - 0.02 for gap, delay in zip(gaps, R.RETRY_DELAYS_S, strict=False))
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
    (tmp_path / "x").write_text("обычный файл", encoding="utf-8")
    r = rtsp_rec(tmp_path / "x" / "записи", Inputs())
    r.start()
    assert wait_for(lambda: not r.snapshot().active, t=2.0)
    assert "диск" in r.snapshot().error


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


# --- просмотр уже держит H.264 камеры: вторым подключением камера не отдаёт (клуб, 2026-11-10) ---


class OneClientPreview:
    """Просмотр по RTSP на файле: настоящий RtspSource, но считается потоком H.264 камеры."""

    def __new__(cls, path, shares=True):
        from ptztracker.video.rtsp import RtspSource

        class _Src(RtspSource):
            shares_h264 = shares

        return _Src(str(path))


def shared_rec(folder, src, inputs=None, **kw):
    inputs = inputs if inputs is not None else Inputs()  # своё подключение камера отвергает
    r = VideoRecorder("Сцена", folder, url=lambda: "rtsp://u:secret@h/x", open_input=inputs,
                      shared=lambda: src if src.shares_h264 else None, **kw)
    return r, inputs


def test_recording_takes_packets_from_h264_preview(tmp_path, h264_file, fast_retry):
    src = OneClientPreview(h264_file)
    r, inputs = shared_rec(tmp_path, src)
    r.start()
    time.sleep(0.1)  # запись уже ждёт пакеты, когда просмотр открывает поток
    src.start()
    try:
        assert wait_for(lambda: len(r.snapshot().paths) >= 2)  # файл-источник пошёл по второму кругу
        s = r.snapshot()
        assert s.active and s.signal and s.error is None
    finally:
        s = finish(r)
        src.stop()
    assert inputs.calls == []  # второго подключения к камере не было
    assert count_frames(s.paths[0]) >= 70  # из 75: копия без пережатия, с первого ключевого кадра


def test_recording_reports_no_signal_while_h264_preview_is_down(tmp_path, fast_retry, monkeypatch):
    monkeypatch.setattr(R, "SHARED_LOSS_S", 0.1)
    src = OneClientPreview("rtsp://127.0.0.1:9/nothing")
    r, inputs = shared_rec(tmp_path, src)
    r.start()
    try:
        assert wait_for(lambda: not r.snapshot().signal)
        assert r.snapshot().active and inputs.calls == []
        assert "нет сигнала" in r.take_note() and r.take_note() is None
    finally:
        finish(r)


def test_light_recording_takes_frames_from_h264_preview(tmp_path, h264_file_50, fast_retry, monkeypatch):
    src = OneClientPreview(h264_file_50)
    r, inputs = shared_rec(tmp_path, src, mode="light")
    src.start()
    t0 = time.monotonic()
    r.start()
    try:
        assert wait_for(lambda: r.snapshot().bytes_total > 0)
        time.sleep(1.0)
    finally:
        s = finish(r)
        took = time.monotonic() - t0
        src.stop()
    assert inputs.calls == [] and s.error is None
    n = count_frames(s.paths[0])
    # около 25 к/с, а не 50. Счёт по времени, которое запись шла на самом деле: на занятой машине «секунда» длиннее
    # (CI macos-15, 2026-11-11: 41 кадр при прежнем пределе 40). При 50 к/с было бы 50·took — заметно больше предела
    assert 5 <= n <= 30 * took + 8, (n, took)


def test_preview_on_mjpeg_keeps_recorders_own_connection(tmp_path, h264_file, fast_retry):
    src = OneClientPreview(h264_file, shares=False)
    r, inputs = shared_rec(tmp_path, src, inputs=Inputs(h264_file))
    r.start()
    assert wait_for(lambda: not r.snapshot().signal and r.snapshot().bytes_total > 0)
    s = finish(r)
    assert len(inputs.calls) >= 1 and count_frames(s.paths[0]) == 75


# --- возврат с «лёгкого файла» к полному качеству (клуб, 2026-11-10: 40 минут записи в 3 Мбит/с из-за сбоя в 0,3 с) ---


class Paced:
    """Контейнер, который отдаёт пакеты не быстрее камеры: у возврата к копии счёт идёт на секунды.

    Пакеты идут по расписанию (k-й — не раньше k·dt от начала), а не «пауза после каждого»: на машине CI пауза 0,01 с
    длится 0,02–0,03 с, и поток шёл бы втрое медленнее задуманного (macos-15, 2026-11-11: ключевой кадр приходил позже
    срока возврата к копии, и лёгкий файл не успевал начаться)."""

    def __init__(self, path, dt):
        self.c, self.dt = av.open(str(path)), dt
        self.streams = self.c.streams

    def demux(self, stream):
        due = time.monotonic()
        for p in self.c.demux(stream):
            due += self.dt
            time.sleep(max(0.0, due - time.monotonic()))
            yield p

    def close(self):
        self.c.close()


def _copy_fails(monkeypatch, bad):
    """CopyWriter, у которого не проходят вызовы с номерами из bad; times — когда запись пробовала копию заново."""
    real, state = R.CopyWriter, FlakyMux(bad)
    state.times = []

    class Flaky(real):
        def write_packet(self, packet):
            state.n += 1
            if state.n in state.bad:
                if self.bytes_written == 0:
                    state.times.append(time.monotonic())
                raise av.error.ArgumentError(22, "non monotonic dts")
            super().write_packet(packet)

    monkeypatch.setattr(R, "CopyWriter", Flaky)
    return state


def test_recording_returns_to_full_quality_after_fallback(tmp_path, h264_file, fast_retry, monkeypatch):
    # 0,2 с: ключевой кадр идёт каждые 0,05 с — лёгкий файл успевает начаться, даже если машина запнулась на 0,1 с
    monkeypatch.setattr(R, "COPY_RETRY_S", 0.2)
    _copy_fails(monkeypatch, set(range(7, 32)))  # 25 пакетов подряд: переход на лёгкий файл; дальше поток здоров
    r = rtsp_rec(tmp_path, Inputs(lambda: Paced(h264_file, 0.01)))
    r.start()
    assert wait_for(lambda: r.snapshot().degraded)
    assert wait_for(lambda: not r.snapshot().degraded and len(r.snapshot().paths) == 3)
    assert wait_for(lambda: not r.snapshot().signal)  # поток кончился
    s = finish(r)
    assert s.error is None and r._mode == "camera"
    light, full = count_frames(s.paths[1]), count_frames(s.paths[2])
    assert light >= 5 and full >= 5  # лёгкий файл закрыт целым, дальше снова копия
    assert light + full >= 35  # из 40 кадров после сбоя: на стыке не теряем больше группы кадров


def test_failed_return_keeps_the_light_file_and_tries_less_often(tmp_path, tmp_path_factory, fast_retry,
                                                                 monkeypatch):
    from video_helpers import make_h264

    monkeypatch.setattr(R, "COPY_RETRY_S", 0.05)
    src = make_h264(tmp_path_factory.mktemp("long") / "long.mp4", seconds=6)
    state = _copy_fails(monkeypatch, set(range(7, 10_000)))  # копия больше не идёт вовсе
    r = rtsp_rec(tmp_path, Inputs(lambda: Paced(src, 0.008)))
    r.start()
    assert wait_for(lambda: not r.snapshot().signal and r.snapshot().degraded, t=8.0)
    s = finish(r)
    assert s.error is None and s.degraded
    assert len(s.paths) == 2  # неудачная попытка не рвёт лёгкий файл и не оставляет пустых
    assert count_frames(s.paths[1]) >= 110  # все кадры после сбоя (115), включая те, на которых пробовали копию
    gaps = [b - a for a, b in zip(state.times, state.times[1:], strict=False)]
    assert len(gaps) >= 2
    assert all(gap >= 0.05 * 2 ** (i + 1) - 0.02 for i, gap in enumerate(gaps))  # 0,1 с, 0,2 с, 0,4 с…


def test_fallback_starts_decoding_from_a_keyframe(tmp_path, h264_file, fast_retry, monkeypatch):
    seen, real = [], av.Packet.decode

    def spy(self, *a, **k):
        seen.append(bool(self.is_keyframe))
        return real(self, *a, **k)

    monkeypatch.setattr(av.Packet, "decode", spy, raising=False)
    _copy_fails(monkeypatch, set(range(7, 32)))  # сбой кончается на пакете 31; пакет 32 — не ключевой
    r = rtsp_rec(tmp_path, Inputs(h264_file))
    r.start()
    assert wait_for(lambda: not r.snapshot().signal and r.snapshot().degraded)
    finish(r)
    monkeypatch.undo()
    assert seen and seen[0]  # декодер не видел кадров, пока шла копия: с середины группы он выдаёт мусор


def test_shared_recording_returns_to_full_quality(tmp_path, h264_file, fast_retry, monkeypatch):
    monkeypatch.setattr(R, "COPY_RETRY_S", 0.2)
    _copy_fails(monkeypatch, set(range(7, 32)))
    src = OneClientPreview(h264_file)
    r, inputs = shared_rec(tmp_path, src)
    r.start()
    time.sleep(0.1)
    src.start()
    try:
        assert wait_for(lambda: r.snapshot().degraded)
        assert wait_for(lambda: not r.snapshot().degraded and r._mode == "camera", t=8.0)
        n = len(r.snapshot().paths)
        assert wait_for(lambda: len(r.snapshot().paths) > n)  # и копия пишется дальше
    finally:
        s = finish(r)
        src.stop()
    assert inputs.calls == [] and s.error is None and not s.degraded


def test_disk_error_on_return_still_closes_the_light_file(tmp_path, h264_file, fast_retry, monkeypatch):
    monkeypatch.setattr(R, "COPY_RETRY_S", 0.2)  # с запасом на запинку машины, как в тесте возврата к копии
    state = _copy_fails(monkeypatch, set(range(7, 32)))
    flaky = R.CopyWriter

    class DiskGone(flaky):
        def write_packet(self, packet):
            if state.n >= 31:
                raise OSError(28, "No space left on device")
            super().write_packet(packet)

    monkeypatch.setattr(R, "CopyWriter", DiskGone)
    r = rtsp_rec(tmp_path, Inputs(lambda: Paced(h264_file, 0.01)))
    r.start()
    assert wait_for(lambda: not r.snapshot().active)
    assert r.wait_closed(5.0)
    s = r.snapshot()
    assert s.error and "диск" in s.error
    assert len(s.paths) == 2 and count_frames(s.paths[1]) >= 5  # лёгкий файл в списке и читается
