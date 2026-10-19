import json
import time
import zipfile

import pytest

from ptztracker.diagnostics.runner import DiagContext, run_diagnostics
from ptztracker.sim.frame_source import SimFrameSource
from ptztracker.sim.http_server import SimHttpServer
from ptztracker.sim.ptz_model import SimPtz
from ptztracker.sim.scene import SyntheticStage


@pytest.fixture
def sim_env():
    sim = SimPtz(min_interval=0.10)
    srv = SimHttpServer(sim)
    srv.start()
    scene = SyntheticStage()
    yield sim, srv, (lambda url: SimFrameSource(sim, scene, latency_s=0.25))
    srv.stop()


def ctx_with(tmp_path, host, factory=None, allow_move=True, username=None, password=None):
    return DiagContext(
        host=host,
        username=username,
        password=password,
        allow_move=allow_move,
        ask_user=lambda msg, t: time.sleep(min(t, 0.2)),
        progress=lambda msg: None,
        out_dir=tmp_path,
        notify_port=0,
        notify_wait_s=1.0,
        source_factory=factory,
    )


def load(zip_path):
    z = zipfile.ZipFile(zip_path)
    return json.loads(z.read("report.json")), z


def check(rep, cid):
    return next(c for c in rep["checks"] if c["id"] == cid)


def test_full_diagnostics_against_sim(tmp_path, sim_env):
    sim, srv, factory = sim_env
    zip_path = run_diagnostics(ctx_with(tmp_path, srv.url_host, factory))
    rep, z = load(zip_path)
    st = {r["id"]: r["status"] for r in rep["checks"]}
    assert st["reachability"] == "ok" and st["rtsp"] == "ok" and st["queries"] == "ok"
    assert 150 <= check(rep, "command_latency")["details"]["median_ms"] <= 1000  # сим. разгон как у HE42
    assert check(rep, "min_interval")["details"]["recommended_s"] == 0.10
    assert st["speed_probe"] == "ok" and st["zoom_probe"] == "ok"
    speeds = check(rep, "speed_probe")["details"]["runs"]
    fast = [r["px_per_s"] for r in speeds if r["code"] == 80]
    slow = [r["px_per_s"] for r in speeds if r["code"] == 55]
    assert min(abs(v) for v in fast) > 3 * max(abs(v) for v in slow)
    assert abs(sim.pan_deg) < 0.5  # камера вернулась
    assert st["notifications"] in ("warn", "fail")  # симулятор уведомления не шлёт — без исключения
    assert "Камера" in z.read("report.txt").decode("utf-8")
    assert any(n.startswith("frames/") for n in z.namelist())
    assert "diag.log" in z.namelist()


def test_no_move_skips_motion_checks(tmp_path, sim_env):
    sim, srv, factory = sim_env
    rep, _ = load(run_diagnostics(ctx_with(tmp_path, srv.url_host, factory, allow_move=False)))
    st = {r["id"]: r["status"] for r in rep["checks"]}
    assert all(st[i] == "skip" for i in ("command_latency", "speed_probe", "zoom_probe"))
    moving = [
        c
        for c in sim.received
        if (c.startswith("#PTS") and c != "#PTS5050")
        or (c.startswith("#Z") and c != "#Z50")
        or c.startswith("#AXZ")
        or (c.startswith("#APC") and len(c) > 4)
    ]
    assert moving == []


def test_unreachable_host_report_still_written(tmp_path):
    t0 = time.monotonic()
    rep, _ = load(run_diagnostics(ctx_with(tmp_path, "10.255.255.1")))
    assert time.monotonic() - t0 < 30
    st = {r["id"]: r["status"] for r in rep["checks"]}
    assert st["reachability"] == "fail"
    assert all(v in ("skip", "fail") for k, v in st.items() if k != "reachability")


def test_password_masked_in_report(tmp_path, sim_env):
    sim, srv, factory = sim_env
    zp = run_diagnostics(ctx_with(tmp_path, srv.url_host, factory, allow_move=False, username="admin", password="S3cr3t!"))
    z = zipfile.ZipFile(zp)
    for name in ("report.json", "report.txt", "diag.log"):
        assert "S3cr3t!" not in z.read(name).decode("utf-8")


def test_diagnostics_latency_per_stream(tmp_path, sim_env):
    sim, srv, factory = sim_env
    rep, z = load(run_diagnostics(ctx_with(tmp_path, srv.url_host, factory)))
    lat = check(rep, "command_latency")["details"]
    assert len(lat["per_stream"]) >= 2
    assert all(150 <= r["median_ms"] <= 1000 for r in lat["per_stream"])
    assert lat["fastest"] in [r["url"] for r in lat["per_stream"]]
    assert "Быстрее всего" in z.read("report.txt").decode("utf-8")
