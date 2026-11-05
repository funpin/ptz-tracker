"""Окно узнаёт итог каждой проверки сразу (DiagContext.on_result), а не только из архива."""

from ptztracker.diagnostics import runner
from ptztracker.diagnostics.checks import CheckResult, DiagContext


def test_on_result_for_each_check_and_skips(tmp_path, monkeypatch):
    def ok(ctx, st):
        st["reachable"] = False
        return CheckResult("reachability", "Связь с камерой", "warn", "медленно")

    monkeypatch.setattr(runner, "ALL_CHECKS", (("reachability", ok, False), ("queries", ok, True)))
    got = []
    ctx = DiagContext(host="h", username=None, password=None, allow_move=False, ask_user=lambda m, s: None,
                      progress=lambda m: None, out_dir=tmp_path, on_result=got.append)
    runner.run_diagnostics(ctx)
    assert [(r.id, r.status) for r in got] == [("reachability", "warn"), ("queries", "skip")]


def test_broken_window_callback_does_not_stop_run(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ALL_CHECKS", (("reachability", lambda c, s: CheckResult("reachability", "С", "ok", ""),
                                                False),))

    def boom(_r):
        raise RuntimeError("окно")

    ctx = DiagContext(host="h", username=None, password=None, allow_move=False, ask_user=lambda m, s: None,
                      progress=lambda m: None, out_dir=tmp_path, on_result=boom)
    assert runner.run_diagnostics(ctx).exists()
