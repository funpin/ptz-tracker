import time

import httpx
import pytest

from ptztracker.camera.client import AuthRequired, CameraClient, CameraUnreachable
from ptztracker.camera.protocol import CameraError


def make(handler, **kw):
    return CameraClient("10.0.0.12", transport=httpx.MockTransport(handler), **kw)


def test_send_ok():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["conn"] = req.headers.get("connection")
        return httpx.Response(200, text="pTS5050")

    assert make(handler).send("#PTS5050") == "pTS5050"
    assert seen["url"] == "http://10.0.0.12/cgi-bin/aw_ptz?cmd=%23PTS5050&res=1"
    assert seen["conn"] == "close"


def test_timeout_raises_unreachable_fast():
    def handler(req):
        raise httpx.ConnectTimeout("timeout", request=req)

    t0 = time.monotonic()
    with pytest.raises(CameraUnreachable):
        make(handler).send("#O")
    assert time.monotonic() - t0 < 1.5


def test_real_unroutable_host_times_out_fast():
    c = CameraClient("10.255.255.1", timeout=0.5)
    t0 = time.monotonic()
    with pytest.raises(CameraUnreachable):
        c.send("#O")
    assert time.monotonic() - t0 < 1.5


def test_401_auth_required():
    with pytest.raises(AuthRequired):
        make(lambda req: httpx.Response(401)).send("#O")


def test_camera_error_passthrough():
    with pytest.raises(CameraError) as e:
        make(lambda req: httpx.Response(200, text="eR1:XYZ")).send("#XYZ")
    assert e.value.code == "er1"


def test_http_500_is_unreachable():
    with pytest.raises(CameraUnreachable):
        make(lambda req: httpx.Response(500)).send("#O")
