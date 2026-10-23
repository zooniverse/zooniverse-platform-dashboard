"""Dash integration test when the optional runtime is available."""

import json

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
