"""Refresh ingestion -> anomaly rebuilding; no network, DB, or Dash dependency."""

from __future__ import annotations

import logging

import pytest

from zooniverse_dashboard import pipeline, refresh

SOURCES = ("users", "classifications", "talks")


def _status(*, fail=None):
    values = {source: "failed" if source == fail else "ok" for source in SOURCES}
    return {"state": "partial_failure" if fail else "ok", "streams": values}


def _catalog():
    return {"sources": {source: {"status": "ok"} for source in SOURCES}}


def test_success_orders_ingestion_before_anomaly_rebuild(monkeypatch, tmp_path):
    calls = []
    steps = []

    def collect(*, root, progress):
        calls.append("collect")
        progress("Fetching users aggregates...")
        return _status()

    def analyze(*, root, progress):
        calls.append("rebuild")
        progress("Analyzing users from saved daily aggregates...")
        return _catalog()

    monkeypatch.setattr(pipeline, "refresh_all", collect)
    monkeypatch.setattr(pipeline, "rebuild", analyze)
    result = pipeline.refresh_and_rebuild(root=tmp_path, progress=steps.append)
    assert calls == ["collect", "rebuild"]
    assert result["state"] == "ok"
    assert steps.index("Fetching users aggregates...") < steps.index(
        "Analyzing users from saved daily aggregates..."
    )


def test_partial_source_failure_still_rebuilds_from_existing_snapshots(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "refresh_all", lambda **kwargs: _status(fail="talks"))

    def analyze(**kwargs):
        calls.append("analyzed")
        return _catalog()

    monkeypatch.setattr(pipeline, "rebuild", analyze)
    result = pipeline.refresh_and_rebuild()
    assert calls == ["analyzed"]
    assert result["state"] == "partial_failure"
    assert result["anomalies_state"] == "ok"
    assert result["sources"]["talks"] == "failed"


def test_cooldown_or_lock_rejection_never_launches_anomaly_build(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "refresh_all",
        lambda **kwargs: (_ for _ in ()).throw(
            refresh.RefreshRejected("Another refresh is already running")
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "rebuild",
        lambda **kwargs: pytest.fail("must not rebuild"),
    )
    with pytest.raises(refresh.RefreshRejected):
        pipeline.refresh_and_rebuild()


def test_analysis_failure_returns_safe_status_without_leaking_secret(monkeypatch, caplog):
    secret = "postgresql://myuser:HIDDEN_PASSWORD@private-host/db"
    monkeypatch.setattr(pipeline, "refresh_all", lambda **kwargs: _status())

    def analyze(**kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(pipeline, "rebuild", analyze)
    with caplog.at_level(logging.INFO, logger="zooniverse_dashboard.refresh"):
        result = pipeline.refresh_and_rebuild()
    assert result["state"] == "partial_failure"
    assert result["anomalies_state"] == "failed"
    assert secret not in caplog.text
    assert "HIDDEN_PASSWORD" not in caplog.text


def test_insufficient_history_in_one_source_is_reported(monkeypatch):
    monkeypatch.setattr(pipeline, "refresh_all", lambda **kwargs: _status())
    doc = _catalog()
    doc["sources"]["talks"] = {"status": "insufficient_history"}
    monkeypatch.setattr(pipeline, "rebuild", lambda **kwargs: doc)
    assert pipeline.refresh_and_rebuild()["state"] == "partial_failure"
