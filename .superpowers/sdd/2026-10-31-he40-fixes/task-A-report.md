# Task A report

Verified: `query()` timeout left the item in `_st.queries`; it was sent later for nobody (and `set_result` on it would have been harmless only because the future was never cancelled).

Implemented (`camera/scheduler.py`):
- `QUERY_MAX_WAIT_S = 0.4`: in `_take_due`, if the chosen item is TRACKING/TRACKING_ZOOM and the oldest query waited >= 0.4 s, the query goes next. EMERGENCY/MANUAL never pre-empted. The heartbeat probe override (`_probe_next`) still applies after it; `_zoom_window_due` needs `best.key == "pt"`, so it is untouched (a query slot costs pt at most one window, once per query).
- `query()` on timeout removes its item from the queue and cancels the future; `_take_due` also purges cancelled queries; `_settle()` skips cancelled futures and swallows InvalidStateError (a query already in flight when abandoned no longer kills the scheduler thread).

TDD: RED (3 new tests failed: starvation, aged-before-tracking, abandoned-sent; emergency/manual test passed as it should); GREEN `tests/camera/test_scheduler.py` 35 passed.
Tests added: starvation (tracking pt+zoom every 40 ms, query sent <= 0.55 s), young query does not pre-empt, aged goes before tracking+zoom, aged never before MANUAL/EMERGENCY (emergency stops still all go first), abandoned not sent, abandoned in flight keeps thread alive.
Fast suite: `.venv/bin/pytest -q -m "not slow"` -> 633 passed, 50 deselected (316 s). ruff clean. test_follower is inside the fast suite, green.
Docs: DECISIONS row (Планировщик команд), CHANGELOG line (Исправлено).
Files: src/ptztracker/camera/scheduler.py, tests/camera/test_scheduler.py, docs/DECISIONS.md, CHANGELOG.md.
