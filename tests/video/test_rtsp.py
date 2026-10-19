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
    assert c[0] == "rtsp://admin:p%40ss@10.0.0.12/MediaInput/h264"
    assert c[-1].startswith("http://")


def test_candidates_without_auth():
    c = rtsp_candidates("10.0.0.12", None, None)
    assert c[0] == "rtsp://10.0.0.12/MediaInput/h264"
    assert len(c) == 5
