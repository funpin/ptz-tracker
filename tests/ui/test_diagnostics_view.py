"""Окно диагностики по макету docs/design/mockups/menu-camera-diag.html: без симулятора, быстро."""

from ptztracker.ui.diagnostics_dialog import DiagnosticsDialog

# --- вид по макету docs/design/mockups/menu-camera-diag.html (без симулятора, быстрые) ----------------


def _fake_run(results, ask=None):
    from ptztracker.diagnostics.checks import TITLES, CheckResult

    def run(ctx):
        for cid, status, summary in results:
            ctx.progress(f"Проверка: {TITLES[cid]}…")
            if ask and cid == ask:
                ctx.ask_user("Подвигайте камеру с пульта", 0.3)
            ctx.on_result(CheckResult(cid, TITLES[cid], status, summary))
        p = ctx.out_dir / "ptz-diag-test.zip"
        p.write_bytes(b"x")
        return p
    return run


def test_checklist_statuses_and_summary(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("ptztracker.ui.diagnostics_dialog.run_diagnostics", _fake_run(
        [("reachability", "ok", "Камера отвечает"), ("rtsp", "warn", "Медленно"), ("queries", "fail", "Не отвечает")]))
    d = DiagnosticsDialog("10.0.0.12", None, None, out_dir=tmp_path)
    qtbot.addWidget(d)
    d.show()
    assert d.start_button.property("variant") == "primary" and d.start_button.text() == "Начать проверку"
    assert [r.status for r in d.checklist.rows][:3] == ["wait", "wait", "wait"]
    d.start_button.click()
    qtbot.waitUntil(lambda: d.result_path is not None, timeout=5000)
    st = {r.cid: r.status for r in d.checklist.rows}
    assert (st["reachability"], st["rtsp"], st["queries"]) == ("ok", "warn", "fail")
    assert "ptz-diag-test.zip" in d.result_label.text()
    assert "1 замечание" in d.result_label.text() and "1 ошибка" in d.result_label.text()
    assert d.open_button.isEnabled() and d.open_button.property("variant") == "primary"
    assert d.start_button.text() == "Повторить"


def test_ask_card_with_countdown(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("ptztracker.ui.diagnostics_dialog.run_diagnostics", _fake_run(
        [("reachability", "ok", "ok"), ("notifications", "ok", "ok")], ask="notifications"))
    d = DiagnosticsDialog("10.0.0.12", None, None, out_dir=tmp_path)
    qtbot.addWidget(d)
    d.show()
    assert not d.ask_card.isVisible()
    d.start_button.click()
    qtbot.waitUntil(lambda: d.ask_card.isVisible(), timeout=3000)
    assert "Подвигайте камеру" in d.ask_title.text() and d.ask_count.text().endswith("с")
    assert d.step_label.text() == "Уведомления и движения с пульта"
    qtbot.waitUntil(lambda: d.result_path is not None, timeout=5000)
    assert not d.ask_card.isVisible()


def test_motion_checks_marked_when_not_allowed(qtbot, tmp_path):
    d = DiagnosticsDialog("10.0.0.12", None, None, out_dir=tmp_path)
    qtbot.addWidget(d)
    hint = {r.cid: r.note for r in d.checklist.rows}
    assert "пропустится" in hint["speed_probe"] and "пропустится" not in hint["reachability"]
    d.allow_move_check.setChecked(True)
    assert "пропустится" not in {r.cid: r.note for r in d.checklist.rows}["speed_probe"]
