"""Refresh logs must be useful without revealing database URLs or secrets."""

from __future__ import annotations

import io
import logging
from datetime import date, datetime, timedelta, timezone

import pytest

from zooniverse_dashboard import refresh
from zooniverse_dashboard.collection_config import set_history_days
from zooniverse_dashboard.logging_utils import (
    LOGGER_NAME,
    configure_refresh_logging,
    safe_error_trace,
)

STAMP = datetime(2026, 10, 23, 13, 0, tzinfo=timezone.utc)


@pytest.fixture
def logged_events():
    logger = logging.getLogger(LOGGER_NAME)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield stream
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def test_configuration_is_idempotent():
    logger = configure_refresh_logging()
    before = len(logger.handlers)
    assert configure_refresh_logging() is logger
    assert len(logger.handlers) == before


def test_safe_trace_never_contains_exception_message_or_secret():
    try:
        raise RuntimeError("postgresql://someone:VERY_SECRET@private-host/db")
    except RuntimeError as exc:
        safe = safe_error_trace(exc)
    assert "VERY_SECRET" not in safe
    assert "private-host" not in safe
    assert "test_safe_trace" in safe


def test_successful_refresh_logs_progress_and_timing(monkeypatch, tmp_path, logged_events):
    set_history_days(3, root=tmp_path)

    def fake_source(source, *, through, root, history_days):
        counts = {through - timedelta(days=i): i + 1 for i in range(history_days)}
        return refresh.publish_snapshot(
            source,
            counts,
            through=through,
            root=root,
            now=STAMP,
            history_days=history_days,
            history_complete=False,
        )

    monkeypatch.setattr(refresh, "refresh_source", fake_source)
    monkeypatch.setattr(refresh, "refresh_launches", lambda **kwargs: None)
    status = refresh.refresh_all(root=tmp_path, now=STAMP)
    events = logged_events.getvalue()
    assert status["state"] == "ok"
    assert "event=refresh_started" in events
    assert "history_days=3" in events
    assert "event=source_succeeded" in events
    assert "event=refresh_finished" in events
    assert "elapsed_ms=" in events
    assert "succeeded=4 failed=0" in events


def test_fetch_logs_bucket_total_and_published_snapshot(monkeypatch, tmp_path, logged_events):
    monkeypatch.setattr(refresh, "fetch_panoptes", lambda *, through, history_days: {through: 123})
    snap = refresh.refresh_source(
        "users", through=date(2026, 10, 22), root=tmp_path, now=STAMP, history_days=1
    )
    assert sum(snap.counts().values()) == 123
    output = logged_events.getvalue()
    assert "event=source_fetch_started source=users" in output
    assert "event=source_fetch_completed source=users buckets=1 aggregate_total=123" in output
    assert "event=snapshot_published source=users" in output
    assert "last_modified_utc=" in output


def test_failure_logs_stage_and_trace_but_never_sensitive_message(
    monkeypatch, tmp_path, logged_events
):
    secret = "postgresql://operator:HIDDEN_PASSWORD@private-server/db"

    def fail(*, through, history_days):
        raise RuntimeError(secret)

    monkeypatch.setattr(refresh, "fetch_panoptes", fail)
    with pytest.raises(RuntimeError):
        refresh.refresh_source(
            "users", through=date(2026, 10, 22), root=tmp_path, now=STAMP, history_days=0
        )
    output = logged_events.getvalue()
    assert "event=source_failed" in output
    assert "phase=fetch_and_validate" in output
    assert "error_type=RuntimeError" in output
    assert "trace=" in output
    assert secret not in output
    assert "HIDDEN_PASSWORD" not in output


def test_partial_failure_logs_summary_without_leaking_secret(monkeypatch, tmp_path, logged_events):
    secret = "token=sensitive-12345"

    def fake_source(source, *, through, root, history_days):
        if source == "talks":
            raise OSError(secret)
        return None

    monkeypatch.setattr(refresh, "refresh_source", fake_source)
    result = refresh.refresh_all(root=tmp_path, now=STAMP)
    output = logged_events.getvalue()
    assert result["state"] == "partial_failure"
    assert "event=source_status_failed" in output
    assert "source=talks error_type=OSError" in output
    assert "state=partial_failure" in output
    assert secret not in output
    with pytest.raises(refresh.RefreshRejected):
        refresh.refresh_all(root=tmp_path, now=STAMP + timedelta(minutes=5))
    assert "event=refresh_rejected" in logged_events.getvalue()
