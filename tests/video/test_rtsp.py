import time

import av
import numpy as np

from ptztracker.video.rtsp import RtspSource, rtsp_candidates


def test_rtsp_bad_url_reports_error_no_exception():
    src = RtspSource("rtsp://127.0.0.1:9/nothing", open_timeout_s=0.5)
    src.start()
    time.sleep(1.5)
    st = src.stats()
    assert st.connected is False and st.error
    assert src.latest() is None
    t0 = time.monotonic()
    src.stop()
    assert time.monotonic() - t0 < 2.5


def _make_mp4(path, w=320, h=240, seconds=2, fps=25):
    with av.open(str(path), "w") as c:
        s = c.add_stream("mpeg4", rate=fps)
        s.width, s.height, s.pix_fmt = w, h, "yuv420p"
        for i in range(seconds * fps):
            img = np.full((h, w, 3), (i * 5) % 255, np.uint8)
            for p in s.encode(av.VideoFrame.from_ndarray(img, format="bgr24")):
                c.mux(p)
        for p in s.encode():
            c.mux(p)


def test_rtsp_reads_local_file_as_stream(tmp_path):
    f = tmp_path / "клип.mp4"
    _make_mp4(f)
    src = RtspSource(str(f))
    src.start()
    try:
        deadline = time.monotonic() + 3
        while src.latest() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        fr = src.latest()
        assert fr is not None and fr.image.shape == (240, 320, 3)
        st = src.stats()
        assert (st.width, st.height) == (320, 240)
    finally:
        src.stop()


def test_candidates_with_auth():
    c = rtsp_candidates("10.0.0.12", "admin", "p@ss")
    # живая AW-HE42 (диагностика 2026-10-19 17:23): MJPEG 344 мс против ~830 мс у H.264 — первым идёт MJPEG
    assert c[0] == "http://admin:p%40ss@10.0.0.12/cgi-bin/mjpeg?resolution=640x360&framerate=25"
    assert c[1] == "rtsp://admin:p%40ss@10.0.0.12/MediaInput/h264/stream_1"
    assert any(u.startswith("http://") for u in c)


def test_candidates_without_auth():
    c = rtsp_candidates("10.0.0.12", None, None)
    assert c[0] == "http://10.0.0.12/cgi-bin/mjpeg?resolution=640x360&framerate=25"
    assert len(c) == 5


def test_rtsp_password_not_shown_or_logged(caplog):
    # Final review I3
    import logging

    caplog.set_level(logging.DEBUG)
    src = RtspSource("rtsp://admin:s3cret@127.0.0.1:9/x", open_timeout_s=0.5)
    src.start()
    time.sleep(1.5)
    st = src.stats()
    src.stop()
    assert st.error and "s3cret" not in st.error
    assert "s3cret" not in st.url
    assert "s3cret" not in caplog.text


def test_rotates_to_next_candidate_and_reports_found(tmp_path):
    # С камеры: первый адрес отвечает 5XX — программа должна сама найти рабочий
    f = tmp_path / "ok.mp4"
    _make_mp4(f)
    found = []
    src = RtspSource(["rtsp://127.0.0.1:9/bad", str(f)], open_timeout_s=0.5, on_found=found.append)
    src.start()
    try:
        deadline = time.monotonic() + 6
        while src.latest() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert src.latest() is not None
        assert src.stats().url == str(f)
        assert found == [str(f)]
    finally:
        src.stop()


def test_error_mentions_which_address_is_tried():
    src = RtspSource(["rtsp://127.0.0.1:9/a", "rtsp://127.0.0.1:9/b"], open_timeout_s=0.3)
    src.start()
    time.sleep(0.8)
    st = src.stats()
    src.stop()
    assert "из 2" in st.error


def test_credentials_helpers():
    from ptztracker.video.rtsp import strip_credentials, with_credentials

    assert with_credentials("rtsp://10.0.0.12/MediaInput/h264", "admin", "p@ss") == "rtsp://admin:p%40ss@10.0.0.12/MediaInput/h264"
    assert with_credentials("rtsp://10.0.0.12/x", None, None) == "rtsp://10.0.0.12/x"
    assert strip_credentials("rtsp://admin:p%40ss@10.0.0.12/x") == "rtsp://10.0.0.12/x"


def test_stream_urls_specific_and_auto():
    from ptztracker.video.rtsp import STREAM_CHOICES, stream_urls

    assert set(STREAM_CHOICES) == {"auto", "mjpeg_1", "mjpeg_2", "h264_1"}
    assert stream_urls("h", "h264_2", "admin", "pw", None) == ["rtsp://admin:pw@h/MediaInput/h264/stream_2"]
    assert stream_urls("h", "h264_1", None, None, "rtsp://h/saved") == ["rtsp://h/MediaInput/h264/stream_1"]
    assert stream_urls("h", "mjpeg_1", None, None, None) == ["http://h/cgi-bin/mjpeg?resolution=640x360&framerate=25"]
    assert stream_urls("h", "mjpeg_2", None, None, None) == ["http://h/cgi-bin/mjpeg?resolution=1280x720&framerate=25"]
    a = stream_urls("h", "auto", None, None, "rtsp://h/saved")
    assert a[0] == "rtsp://h/saved" and len(a) > 1 and len(a) == len(set(a))


def test_rtsp_source_exposes_urls():
    src = RtspSource(["rtsp://a/x", "rtsp://b/y"])
    assert src.urls == ["rtsp://a/x", "rtsp://b/y"]


def test_redact_url_hides_password_with_at_sign():
    from ptztracker.video.rtsp import redact_url

    assert redact_url("rtsp://admin:secret@10.0.0.5/MediaInput/h264") == "rtsp://admin:***@10.0.0.5/MediaInput/h264"
    assert redact_url("rtsp://admin:p@ss@10.0.0.5/x") == "rtsp://admin:***@10.0.0.5/x"
    assert redact_url("http://admin:a@b@c@10.0.0.5:80/cgi-bin/mjpeg") == "http://admin:***@10.0.0.5:80/cgi-bin/mjpeg"
    assert redact_url("http://10.0.0.5/cgi-bin/mjpeg?resolution=640x360") == "http://10.0.0.5/cgi-bin/mjpeg?resolution=640x360"
    assert redact_url("rtsp://10.0.0.5:554/x/a@b") == "rtsp://10.0.0.5:554/x/a@b"
    # внутри JSON адрес кончается на кавычке: соседние поля не задеваются
    line = '{"u": "http://h:80/x", "mail": "a@b", "v": "rtsp://u:p@q@h/"}'
    assert redact_url(line) == '{"u": "http://h:80/x", "mail": "a@b", "v": "rtsp://u:***@h/"}'


def test_mjpeg_2_url_has_framerate_and_label():
    from ptztracker.video.rtsp import STREAM_CHOICES, stream_url, stream_urls

    assert "framerate=" in stream_url("h", "mjpeg_2")
    assert "resolution=1280x720" in stream_url("h", "mjpeg_2")
    assert all("framerate=" in u for u in stream_urls("h", "mjpeg_2", None, None, None))
    assert STREAM_CHOICES["mjpeg_2"] == "MJPEG (2) · 1280×720, 12 к/с"


def _wait(cond, t=5.0):
    end = time.monotonic() + t
    while time.monotonic() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_tap_gets_packets_while_preview_decodes(tmp_path):
    """Камера отдаёт H.264 одному получателю (клуб, 2026-11-10): запись берёт пакеты у просмотра."""
    f = tmp_path / "клип.mp4"
    _make_mp4(f)
    src = RtspSource(str(f))
    tap = src.tap()
    src.start()
    try:
        got = []
        assert _wait(lambda: (got.append(tap.get(0.05)) or True) and any(g and g[0] == 2 for g in got))
        first = [g for g in got if g and g[0] == 1]
        # все пакеты первого прохода файла (50 кадров), кроме первого: его съедает открытие потока (fflags=nobuffer)
        assert len(first) == 49 and [p.dts for _, p, _ in first] == sorted(p.dts for _, p, _ in first)
        assert all(p.size > 0 for _, p, _ in first)
        assert src.latest() is not None  # просмотр при этом декодирует как раньше
    finally:
        src.untap(tap)
        src.stop()


def test_tap_packets_of_closed_connection_cannot_start_a_file(tmp_path):
    """После закрытия подключения его поток трогать нельзя (PyAV: «Container is not open»)."""
    f = tmp_path / "клип.mp4"
    _make_mp4(f, seconds=1)
    src = RtspSource(str(f))
    tap = src.tap()
    src.start()
    try:
        assert _wait(lambda: tap.retired >= 1)  # первый проход файла закрыт
        with tap.lock:
            assert not tap.alive(1) and tap.alive(tap.retired + 1)
    finally:
        src.untap(tap)
        src.stop()


def test_shares_h264_only_for_a_settled_rtsp_address():
    assert RtspSource("rtsp://10.0.0.5/MediaInput/h264/stream_1").shares_h264
    assert not RtspSource("http://10.0.0.5/cgi-bin/mjpeg?resolution=640x360").shares_h264
    assert not RtspSource(["rtsp://10.0.0.5/a", "http://10.0.0.5/b"]).shares_h264  # «Авто»: адрес ещё не найден


def test_wait_joins_a_reader_that_outlived_stop(monkeypatch):
    """Падение при выходе (отчёт macOS 2026-11-13, EXC_BAD_ACCESS в PyGILState_Ensure): поток приёма ещё читал
    поток камеры, когда Python уже завершался. stop(0) потока не ждёт (окно не должно виснуть) — перед выходом из
    программы его дожидается wait()."""
    import threading

    release = threading.Event()
    src = RtspSource("rtsp://127.0.0.1:9/x")
    monkeypatch.setattr(src, "_run", lambda: release.wait(5))
    src.start()
    src.stop(timeout=0.0)
    assert src.wait(0.05) is False  # ещё читает
    release.set()
    assert src.wait(2.0) is True
    assert src.wait(0.0) is True  # ждать больше некого
