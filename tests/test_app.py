"""Dash integration test when the optional runtime is available."""

import json
from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest


def test_root_redirect_and_refresh_control():
    pytest.importorskip("dash")
    from zooniverse_dashboard.app import app

    client = app.server.test_client()
    response = client.get("/")
    assert response.status_code in (301, 302, 307, 308)
    assert "/users" in response.headers.get("Location", "")
    layout = client.get("/_dash-layout")
    assert layout.status_code == 200
    assert b'"refresh-now"' in layout.data
    ids = json.dumps(layout.get_json())
    assert "Overview" not in ids
    assert "Data sources" not in ids
    assert "refresh-success" in ids


def test_refresh_progress_hides_after_success_and_shows_short_confirmation(monkeypatch):
    """An automatic reload must not reopen the completed checklist."""
    pytest.importorskip("dash")
    module = import_module("zooniverse_dashboard.app")
    run_id = "a" * 32
    job = {
        "run_id": run_id,
        "state": "ok",
        "steps": ["Users updated", "Classifications updated", "Talk updated"],
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    monkeypatch.setattr(module, "load_job", lambda: job)
    callbacks = [
        entry["callback"].__wrapped__
        for output, entry in module.app.callback_map.items()
        if "refresh-steps.children" in output
    ]
    assert len(callbacks) == 1
    progress = callbacks[0]

    steps, panel, disabled, completion, confirmation, banner = progress(1, run_id)
    assert panel == {"display": "none"}
    assert not disabled
    assert steps == ""
    assert completion == {"reload": True, "run_id": run_id}
    assert "Data refreshed successfully" in confirmation
    assert banner == {"display": "block"}

    # After a minute, no obsolete success message remains on other pages.
    job["finished_at_utc"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    _, panel, _, completion, confirmation, banner = progress(2, run_id)
    assert panel == banner == {"display": "none"}
    assert completion is None
    assert confirmation == ""

    # While the job is executing, show detailed steps and disable the button.
    job["state"] = "running"
    _, panel, disabled, completion, confirmation, banner = progress(3, run_id)
    assert panel == {"display": "block"}
    assert disabled
    assert completion is None
    assert confirmation == ""
    assert banner == {"display": "none"}

    # A failed run preserves the actionable progress details instead.
    job["state"] = "partial_failure"
    _, panel, disabled, completion, confirmation, banner = progress(3, run_id)
    assert panel == {"display": "block"}
    assert not disabled
    assert completion is None
    assert confirmation == ""
    assert banner == {"display": "none"}

    # The compact confirmation is scoped to the initiating browser session.
    job["state"] = "ok"
    job["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    _, panel, _, completion, confirmation, banner = progress(4, "b" * 32)
    assert panel == banner == {"display": "none"}
    assert completion is None
    assert confirmation == ""
