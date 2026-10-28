"""Dash integration test when the optional runtime is available."""

import json
from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest


def test_gunicorn_entrypoint_exposes_wsgi_server():
    """The Docker CMD uses `gunicorn app:server`, not Dash's `app` object."""
    pytest.importorskip("dash")
    entrypoint = import_module("app")
    application = import_module("zooniverse_dashboard.app")
    assert entrypoint.server is application.server


def test_root_redirect_and_refresh_control():
    pytest.importorskip("dash")
    from zooniverse_dashboard.app import app

    client = app.server.test_client()
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.get_json() == {"status": "ok"}
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

    # Idle tabs must not issue one POST every second.
    def components(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from components(value)
        elif isinstance(node, list):
            for value in node:
                yield from components(value)

    fast_poll = next(
        node
        for node in components(layout.get_json())
        if node.get("props", {}).get("id") == "refresh-progress-poll"
    )
    assert fast_poll["props"]["disabled"] is True


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
    # Both local job admission and the slow status tick can wake up polling.
    callback = next(
        entry
        for output, entry in module.app.callback_map.items()
        if "refresh-steps.children" in output
    )
    assert {item["id"] for item in callback["inputs"]} == {
        "refresh-progress-poll",
        "refresh-run",
        "refresh-status-poll",
    }

    steps, panel, disabled, completion, confirmation, banner, poll_disabled = progress(1, run_id, 0)
    assert poll_disabled is True
    assert panel == {"display": "none"}
    assert not disabled
    assert steps == ""
    assert completion == {"reload": True, "run_id": run_id}
    assert "Data refreshed successfully" in confirmation
    assert banner == {"display": "block"}

    # After a minute, no obsolete success message remains on other pages.
    job["finished_at_utc"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    _, panel, _, completion, confirmation, banner, poll_disabled = progress(2, run_id, 1)
    assert poll_disabled is True
    assert panel == banner == {"display": "none"}
    assert completion is None
    assert confirmation == ""

    # While the job is executing, show detailed steps and disable the button.
    job["state"] = "running"
    _, panel, disabled, completion, confirmation, banner, poll_disabled = progress(3, run_id, 1)
    assert panel == {"display": "block"}
    assert disabled
    assert poll_disabled is False
    assert completion is None
    assert confirmation == ""
    assert banner == {"display": "none"}

    # A failed run preserves the actionable progress details instead.
    job["state"] = "partial_failure"
    _, panel, disabled, completion, confirmation, banner, poll_disabled = progress(3, run_id, 1)
    assert panel == {"display": "block"}
    assert not disabled
    assert poll_disabled is True
    assert completion is None
    assert confirmation == ""
    assert banner == {"display": "none"}

    # The compact confirmation is scoped to the initiating browser session.
    job["state"] = "ok"
    job["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    _, panel, _, completion, confirmation, banner, poll_disabled = progress(4, "b" * 32, 2)
    assert panel == banner == {"display": "none"}
    assert completion is None
    assert confirmation == ""
    assert poll_disabled is True

    # A different session can start a job while this tab is idle. The slow
    # status tick must wake fast polling without requiring a local click.
    job["state"] = "queued"
    _, panel, disabled, _, _, banner, poll_disabled = progress(4, "b" * 32, 3)
    assert panel == {"display": "block"}
    assert disabled
    assert poll_disabled is False
    assert banner == {"display": "none"}

    job["state"] = "interrupted"
    _, panel, disabled, _, _, _, poll_disabled = progress(5, run_id, 3)
    assert panel == {"display": "block"}
    assert not disabled
    assert poll_disabled is True

    # A new tab with no remembered run ID still observes active jobs.
    job["state"] = "running"
    _, panel, disabled, _, _, _, poll_disabled = progress(0, None, 0)
    assert panel == {"display": "block"}
    assert disabled
    assert poll_disabled is False

    monkeypatch.setattr(module, "load_job", lambda: None)
    _, panel, disabled, _, _, _, poll_disabled = progress(0, None, 1)
    assert panel == {"display": "none"}
    assert not disabled
    assert poll_disabled is True
