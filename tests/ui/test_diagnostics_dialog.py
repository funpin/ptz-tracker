from pathlib import Path

import pytest

from ptztracker.sim.http_server import SimHttpServer
from ptztracker.sim.ptz_model import SimPtz
from ptztracker.ui.diagnostics_dialog import DiagnosticsDialog

pytestmark = pytest.mark.slow  # реальное время на симуляторе


def test_dialog_runs_and_shows_path(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("ptztracker.diagnostics.checks.RTSP_WAIT_S", 1.5)
    srv = SimHttpServer(SimPtz())
    srv.start()
    try:
        d = DiagnosticsDialog(srv.url_host, None, None, out_dir=tmp_path, notify_port=0, notify_wait_s=0.5)
        qtbot.addWidget(d)
        assert not d.allow_move_check.isChecked()
        d.start_button.click()
        qtbot.waitUntil(lambda: d.result_path is not None, timeout=90000)
        assert Path(d.result_path).exists()
        assert d.open_button.isEnabled()
        assert "ptz-diag-" in d.result_label.text()
    finally:
        srv.stop()
