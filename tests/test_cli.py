import builtins

import pytest

from ptztracker.__main__ import main
from ptztracker.sim.http_server import SimHttpServer
from ptztracker.sim.ptz_model import SimPtz


def test_cli_requires_ip(capsys):
    assert main(["--diagnose"]) == 2


@pytest.mark.slow
def test_cli_against_sim(tmp_path, monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "д")
    monkeypatch.setattr("ptztracker.diagnostics.checks.RTSP_WAIT_S", 1.5)
    srv = SimHttpServer(SimPtz())
    srv.start()
    try:
        rc = main(["--diagnose", "--ip", srv.url_host, "--no-move", "--out", str(tmp_path), "--notify-wait", "0.5", "--notify-port", "0"])
    finally:
        srv.stop()
    assert rc == 0
    assert list(tmp_path.glob("ptz-diag-*.zip"))


def test_bad_args_exit_2():
    with pytest.raises(SystemExit) as e:
        main(["--no-such-flag"])
    assert e.value.code == 2


def test_diag_main_prompts_for_login(monkeypatch):
    # Final review (повышено до Important): запуск двойным кликом на камере с паролем
    import ptztracker.__main__ as m

    answers = iter(["10.0.0.5", "admin"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda *a: "pw")
    monkeypatch.setattr("sys.argv", ["PTZTracker-diag"])
    seen = {}
    monkeypatch.setattr(m, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    m.diag_main()
    a = seen["argv"]
    assert a[a.index("--ip") + 1] == "10.0.0.5"
    assert a[a.index("--user") + 1] == "admin" and a[a.index("--password") + 1] == "pw"
