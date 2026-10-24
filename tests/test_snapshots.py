"""Aggregate-only snapshot contract, persistence, freshness and privacy."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from zooniverse_dashboard.periods import calendar_periods, visible_date_range
from zooniverse_dashboard.snapshots import load_snapshot, publish_snapshot

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def sample(days=28):
    first = date(2026, 9, 25)
    return {first + timedelta(days=i): i for i in range(days)}


def test_publication_roundtrip_and_last_modified(tmp_path):
    values = sample()
    snap = publish_snapshot("users", values, through=max(values), root=tmp_path, now=NOW)
    read = load_snapshot("users", root=tmp_path)
    assert snap == read
    assert read.last_modified == NOW
    doc = json.loads((tmp_path / "users.json").read_text())
    assert doc["last_modified_utc"] == NOW.isoformat()
    assert set(doc) == {
        "format",
        "source",
        "first",
        "last",
        "as_of",
        "last_modified_utc",
        "daily",
        "history_complete",
        "history_days",
    }
    assert "email" not in str(doc).lower()
    again = NOW + timedelta(minutes=5)
    publish_snapshot("users", values, through=max(values), root=tmp_path, now=again)
    assert load_snapshot("users", root=tmp_path).last_modified == again


def test_reject_missing_days_and_invalid_counts(tmp_path):
    values = sample()
    values.pop(date(2026, 10, 1))
    with pytest.raises(ValueError):
        publish_snapshot("users", values, through=max(values), root=tmp_path, now=NOW)
    with pytest.raises(ValueError):
        publish_snapshot(
            "users", {date(2026, 10, 22): -1}, through=date(2026, 10, 22), root=tmp_path, now=NOW
        )
    assert not (tmp_path / "users.json").exists()


def test_corrupt_destination_preserved(tmp_path):
    path = tmp_path / "users.json"
    path.write_text("not a valid snapshot")
    with pytest.raises(ValueError):
        publish_snapshot("users", sample(), through=date(2026, 10, 22), root=tmp_path, now=NOW)
    assert path.read_text() == "not a valid snapshot"


def test_reject_symlink(tmp_path):
    target = tmp_path / "target"
    target.write_text("untouched")
    (tmp_path / "users.json").symlink_to(target)
    with pytest.raises(ValueError):
        publish_snapshot(
            "users", {date(2026, 10, 22): 2}, through=date(2026, 10, 22), root=tmp_path, now=NOW
        )
    assert target.read_text() == "untouched"


def test_calendar_periods_and_panning(tmp_path):
    first = date(2025, 1, 1)
    through = date(2026, 10, 22)
    counts = {first + timedelta(days=i): 1 for i in range((through - first).days + 1)}
    snap = publish_snapshot("users", counts, through=through, root=tmp_path, now=NOW)
    periods = {p.key: p for p in calendar_periods(snap)}
    assert periods["yesterday"].count == 1
    assert periods["week"].count == 4
    assert periods["previous-week"].count == 7
    assert periods["month"].count == 22
    assert periods["previous-month"].count == 30
    assert periods["year"].count == (through - date(2026, 1, 1)).days + 1
    assert periods["previous-year"].count == 365
    assert visible_date_range(first=snap.first, last=snap.last, window="7") == (
        date(2026, 10, 16),
        date(2026, 10, 22),
    )


def test_incomplete_history_is_unavailable(tmp_path):
    snap = publish_snapshot("talks", sample(), through=date(2026, 10, 22), root=tmp_path, now=NOW)
    periods = {p.key: p for p in calendar_periods(snap)}
    assert periods["previous-year"].count is None


def test_legacy_snapshot_requires_history_upgrade(tmp_path):
    values = sample()
    publish_snapshot("classifications", values, through=max(values), root=tmp_path, now=NOW)
    path = tmp_path / "classifications.json"
    doc = json.loads(path.read_text())
    del doc["history_complete"]
    path.write_text(json.dumps(doc))
    snap = load_snapshot("classifications", root=tmp_path)
    assert snap.history_complete is False
    doc["history_complete"] = "yes"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="history coverage"):
        load_snapshot("classifications", root=tmp_path)


def test_history_limit_is_retained_and_validated(tmp_path):
    values = sample(days=28)
    snap = publish_snapshot(
        "talks",
        values,
        through=max(values),
        root=tmp_path,
        now=NOW,
        history_complete=False,
        history_days=28,
    )
    assert snap.history_days == 28
    assert not snap.history_complete
    path = tmp_path / "talks.json"
    doc = json.loads(path.read_text())
    doc["history_days"] = 7
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="range"):
        load_snapshot("talks", root=tmp_path)
