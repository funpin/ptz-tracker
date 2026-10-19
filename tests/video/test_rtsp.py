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
    # подтверждено на живой AW-HE42 (диагностика 2026-10-19): работает /MediaInput/h264/stream_1
    assert c[0] == "rtsp://admin:p%40ss@10.0.0.12/MediaInput/h264/stream_1"
    assert any(u.startswith("http://") for u in c)


def test_candidates_without_auth():
    c = rtsp_candidates("10.0.0.12", None, None)
    assert c[0] == "rtsp://10.0.0.12/MediaInput/h264/stream_1"
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

    assert set(STREAM_CHOICES) == {"auto", "h264_1", "h264_2", "mjpeg"}
    assert stream_urls("h", "h264_2", "admin", "pw", None) == ["rtsp://admin:pw@h/MediaInput/h264/stream_2"]
    assert stream_urls("h", "h264_1", None, None, "rtsp://h/saved") == ["rtsp://h/MediaInput/h264/stream_1"]
    assert stream_urls("h", "mjpeg", None, None, None) == ["http://h/cgi-bin/mjpeg?resolution=640x360&framerate=25"]
    a = stream_urls("h", "auto", None, None, "rtsp://h/saved")
    assert a[0] == "rtsp://h/saved" and len(a) > 1 and len(a) == len(set(a))


def test_rtsp_source_exposes_urls():
    src = RtspSource(["rtsp://a/x", "rtsp://b/y"])
    assert src.urls == ["rtsp://a/x", "rtsp://b/y"]
