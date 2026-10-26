"""Offline anomaly catalog, past-only scoring, and navigation contracts."""

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from zooniverse_dashboard.anomalies import catalog, compute
from zooniverse_dashboard.snapshots import publish_snapshot


def test_episode_grouping_independent_evidence_and_shared_drops():
    days = [date(2026, 2, 1) + timedelta(days=i) for i in range(9)]
    scores = [
        {
            "date": day.isoformat(),
            "observed": 20 if i in (2, 3, 4) else 100,
            "expected": 100.0,
            "raw_p": 0.0001 if i == 2 else (0.007 if i in (3, 4) else 0.4),
            "empirical_p": 0.005 if i == 3 else 0.5,
            "direction": "drop" if i in (2, 3, 4) else "spike",
        }
        for i, day in enumerate(days)
    ]
    episodes = compute.episodes(scores, source="talks")
    assert len(episodes) == 1
    event = episodes[0]
    assert event["id"] == "talks-2026-02-03-drop"
    assert event["detected"] == "2026-02-03"
    assert event["end"] == "2026-02-05"
    assert event["observed"] == 60
    assert event["expected"] == 300
    assert event["algorithms"] == ["NB2", "Rolling historical rarity (365d)"]
    other = {**event, "source": "users", "id": "users-2026-02-03-drop"}
    compute.add_shared_drop_context([event, other])
    assert "users" in event["context"][0]
    spike = {**event, "direction": "spike", "context": []}
    compute.add_shared_drop_context([spike, {**other, "direction": "spike"}])
    assert spike["context"] == []


def test_catalog_revision_and_stale_results(tmp_path):
    through = date(2026, 5, 31)
    first = through - timedelta(days=9)
    daily = {first + timedelta(days=i): i + 1 for i in range(10)}
    first_stamp = datetime(2026, 6, 1, tzinfo=timezone.utc)
    snapshot = publish_snapshot("users", daily, through=through, root=tmp_path, now=first_stamp)
    event = {
        "id": "users-2026-05-26-spike",
        "source": "users",
        "start": "2026-05-26",
        "end": "2026-05-26",
        "detected": "2026-05-26",
        "direction": "spike",
    }
    doc = {
        "format": catalog.CATALOG_FORMAT,
        "sources": {
            "users": {"status": "ok", "snapshot_revision": snapshot.last_modified.isoformat()}
        },
        "events": [event],
    }
    catalog.write_catalog(doc, root=tmp_path)
    events, status = catalog.visible_events(root=tmp_path)
    assert events == [event]
    assert status["users"] == "ready"
    assert status["talks"] == "snapshot unavailable"
    assert catalog.read_catalog(root=tmp_path) == doc
    publish_snapshot(
        "users", daily, through=through, root=tmp_path, now=first_stamp + timedelta(seconds=3)
    )
    events, status = catalog.visible_events(root=tmp_path)
    assert not events
    assert "stale" in status["users"]


def test_no_history_no_events_and_validated_identifiers():
    assert compute.score_days((), source="users") == []
    assert compute.episodes([], source="users") == []
    assert catalog.ID_PATTERN.fullmatch("users-2026-02-03-spike")
    assert not catalog.ID_PATTERN.fullmatch("/etc/passwd")


def test_forecasts_have_no_future_leakage(monkeypatch):
    # Deterministic fit isolates refit scheduling and calibration logic.
    def mock_fit(y, x):
        return np.asarray([np.log(100.0)] + [0.0] * (x.shape[1] - 1)), 0.15

    monkeypatch.setattr(compute, "_fit_nb2", mock_fit)
    first = date(2022, 1, 1)
    rows = tuple((first + timedelta(days=i), 100 + int(i % 7 == 3) * 20) for i in range(1900))
    earlier = compute.score_days(rows[:1800], source="users")
    later = compute.score_days(rows, source="users")
    overlap = {score["date"]: score for score in later}
    assert earlier
    compared = [score for score in earlier if score["date"] in overlap]
    assert len(compared) > 600
    for score in compared:
        assert overlap[score["date"]] == score


def test_profile_nb2_real_fit():
    pytest.importorskip("statsmodels")
    rng = np.random.default_rng(42)
    days = tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(280))
    x = compute.design(days, days[0], calendar=False)
    mu = np.exp(x @ np.array([np.log(100.0), 0.05, 0, 0, 0, 0, 0, 0]))
    y = rng.negative_binomial(12, 12 / (12 + mu)).astype(float)
    beta, alpha = compute._fit_nb2(y, x)
    assert len(beta) == x.shape[1]
    assert 0 < alpha < 8
    assert np.all(np.isfinite(beta))
