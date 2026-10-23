"""Read-only refresh safety and isolation: no real network/database connection."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from zooniverse_dashboard import refresh
from zooniverse_dashboard.eras import ClassificationProbe
from zooniverse_dashboard.snapshots import load_snapshot

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def fake_probe(start, end):
    days = (end - start).days + 1
    return ClassificationProbe(
        start, end, days * 17, tuple((start + timedelta(days=i), 17) for i in range(days))
    )


def fake_full_probe(through):
    first = date(2024, 1, 1)
    return fake_probe(first, through)


def test_initial_eras_refresh_all_available_history(monkeypatch, tmp_path):
    calls = []

    def full(through):
        calls.append(through)
        return fake_full_probe(through)

    monkeypatch.setattr(refresh, "fetch_full_classifications", full)
    snap = refresh.refresh_source(
        "classifications", through=date(2026, 10, 22), root=tmp_path, now=NOW
    )
    assert snap.first == date(2024, 1, 1)
    assert snap.history_complete is True
    assert calls == [date(2026, 10, 22)]
    assert sum(snap.counts().values()) == (date(2026, 10, 22) - snap.first).days * 17 + 17


def test_eras_reconciliation_changes_counts_without_full_refetch(monkeypatch, tmp_path):
    full_calls = []

    def full(through):
        full_calls.append(through)
        return fake_full_probe(through)

    monkeypatch.setattr(refresh, "fetch_full_comments", full)
    through = date(2026, 10, 22)
    refresh.refresh_source("talks", through=through, root=tmp_path, now=NOW)
    recent_calls = []

    def modified(start, end):
        recent_calls.append((start, end))
        p = fake_probe(start, end)
        rows = list(p.observed)
        rows[-1] = (rows[-1][0], 22)
        return ClassificationProbe(start, end, p.reported_total + 5, tuple(rows))

    monkeypatch.setattr(refresh, "fetch_comments", modified)
    refresh.refresh_source("talks", through=through, root=tmp_path, now=NOW + timedelta(minutes=5))
    assert load_snapshot("talks", root=tmp_path).daily[-1][1] == 22
    assert len(recent_calls) == 1
    assert recent_calls[0][0] == through - timedelta(days=13)
    assert full_calls == [through]


def test_rejected_eras_result_keeps_previous_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh, "fetch_full_classifications", fake_full_probe)
    refresh.refresh_source("classifications", through=date(2026, 10, 22), root=tmp_path, now=NOW)
    before = (tmp_path / "classifications.json").read_bytes()

    def bad(_start, _end):
        raise ValueError("fake secret should not persist")

    monkeypatch.setattr(refresh, "fetch_classifications", bad)
    with pytest.raises(ValueError):
        refresh.refresh_source(
            "classifications", through=date(2026, 10, 22), root=tmp_path, now=NOW
        )
    assert (tmp_path / "classifications.json").read_bytes() == before


def test_legacy_partial_snapshot_upgrades_atomically(monkeypatch, tmp_path):
    from zooniverse_dashboard.snapshots import publish_snapshot

    recent = fake_probe(date(2026, 10, 1), date(2026, 10, 22))
    publish_snapshot(
        "talks",
        dict(recent.calendar_series()),
        through=recent.end,
        root=tmp_path,
        now=NOW,
        history_complete=False,
    )
    before = (tmp_path / "talks.json").read_bytes()

    def failing(_through):
        raise ValueError("Fake ERAS failure")

    monkeypatch.setattr(refresh, "fetch_full_comments", failing)
    with pytest.raises(ValueError):
        refresh.refresh_source("talks", through=recent.end, root=tmp_path, now=NOW)
    assert (tmp_path / "talks.json").read_bytes() == before

    monkeypatch.setattr(refresh, "fetch_full_comments", fake_full_probe)
    updated = refresh.refresh_source("talks", through=recent.end, root=tmp_path, now=NOW)
    assert updated.first == date(2024, 1, 1)
    assert updated.history_complete


def test_date_limited_backfill_is_not_claimed_as_all_time(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh, "fetch_classifications", fake_probe)
    snapshot = refresh.refresh_source(
        "classifications",
        through=date(2026, 10, 22),
        backfill_start=date(2026, 9, 1),
        root=tmp_path,
        now=NOW,
    )
    assert snapshot.first == date(2026, 9, 1)
    assert not snapshot.history_complete


def test_all_streams_status_and_cooldown(monkeypatch, tmp_path):
    def fake_source(source, *, through, root, now=None):
        assert source in ("users", "classifications", "talks")
        assert through == date(2026, 10, 22)

    monkeypatch.setattr(refresh, "refresh_source", fake_source)
    status = refresh.refresh_all(root=tmp_path, now=NOW)
    assert status["state"] == "ok"
    with pytest.raises(refresh.RefreshRejected, match="last hour"):
        refresh.refresh_all(root=tmp_path, now=NOW + timedelta(minutes=5))
    assert refresh.load_status(tmp_path)["state"] == "ok"


def test_failed_source_is_sanitized(monkeypatch, tmp_path):
    def failing(source, **kwargs):
        if source == "users":
            raise ValueError("secret://credentials@example.com")

    monkeypatch.setattr(refresh, "refresh_source", failing)
    status = refresh.refresh_all(root=tmp_path, now=NOW)
    assert status["state"] == "partial_failure"
    content = (tmp_path / "refresh_status.json").read_text()
    assert "secret" not in content
    assert json.loads(content)["streams"]["users"] == "failed"


def test_lock_rejects_overlap(tmp_path):
    with refresh.refresh_lock(tmp_path):
        with pytest.raises(refresh.RefreshRejected, match="already running"):
            with refresh.refresh_lock(tmp_path):
                pass
