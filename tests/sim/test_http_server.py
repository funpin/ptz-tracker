import httpx

from ptztracker.camera.client import CameraClient
from ptztracker.sim.http_server import SimHttpServer
from ptztracker.sim.ptz_model import SimPtz


def test_http_server_with_real_client():
    srv = SimHttpServer(SimPtz())
    srv.start()
    try:
        c = CameraClient(srv.url_host)
        assert c.send("#O") == "p1"
        assert c.send("QID") == "OID:AW-HE42"
    finally:
        srv.stop()


def test_server_moves_camera_in_real_time():
    import time

    sim = SimPtz()
    srv = SimHttpServer(sim)
    srv.start()
    try:
        CameraClient(srv.url_host).send("#PTS9950")
        time.sleep(0.3)
        assert sim.pan_deg > 2
    finally:
        srv.stop()


def test_unknown_path_404():
    srv = SimHttpServer(SimPtz())
    srv.start()
    try:
        r = httpx.get(f"http://{srv.url_host}/cgi-bin/event?connect=start")
        assert r.status_code == 404
    finally:
        srv.stop()
