"""On-demand refresh never uses cached count deltas; safety and lock tests."""

from datetime import date, datetime, timedelta, timezone

import pytest

from zooniverse_dashboard import refresh
from zooniverse_dashboard.collection_config import set_history_days
from zooniverse_dashboard.eras import ClassificationProbe

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
THROUGH = date(2026, 10, 22)


def fake_probe(start, end, count=17):
    days = (end - start).days + 1
    return ClassificationProbe(
        start,
        end,
        days * count,
        tuple((start + timedelta(days=i), count) for i in range(days)),
    )


def fake_full_probe(through):
    return fake_probe(date(2024, 1, 1), through)


def test_all_time_eras_is_refetched_on_every_refresh(monkeypatch, tmp_path):
    calls = []

    def fetch(through):
        calls.append(through)
        return fake_full_probe(through)

    monkeypatch.setattr(refresh, "fetch_full_classifications", fetch)
    first = refresh.refresh_source("classifications", through=THROUGH, root=tmp_path, now=NOW)
    second = refresh.refresh_source(
        "classifications", through=THROUGH, root=tmp_path, now=NOW + timedelta(minutes=5)
    )
    assert first.history_complete is True
    assert second.first == date(2024, 1, 1)
    assert second.history_days == 0
    assert calls == [THROUGH, THROUGH]


def test_bounded_eras_refetch_replaces_snapshot_and_dates(monkeypatch, tmp_path):
    set_history_days(28, root=tmp_path)
    calls = []

    def fetch(start, end):
        calls.append((start, end))
        return fake_probe(start, end, 20 if len(calls) == 2 else 17)

    monkeypatch.setattr(refresh, "fetch_window_comments", fetch)
    first = refresh.refresh_source("talks", through=THROUGH, root=tmp_path, now=NOW)
    second = refresh.refresh_source(
        "talks", through=THROUGH, root=tmp_path, now=NOW + timedelta(minutes=5)
    )
    expected_start = THROUGH - timedelta(days=27)
    assert calls == [(expected_start, THROUGH), (expected_start, THROUGH)]
    assert first.first == expected_start
    assert second.first == expected_start
    assert not second.history_complete
    assert second.history_days == 28
    assert sum(second.counts().values()) == 28 * 20


def test_limit_change_discards_previous_window_after_success(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh, "fetch_full_classifications", fake_full_probe)
    old = refresh.refresh_source("classifications", through=THROUGH, root=tmp_path, now=NOW)
    assert old.history_days == 0
    set_history_days(7, root=tmp_path)
    monkeypatch.setattr(
        refresh, "fetch_window_classifications", lambda start, end: fake_probe(start, end)
    )
    new = refresh.refresh_source("classifications", through=THROUGH, root=tmp_path, now=NOW)
    assert new.first == THROUGH - timedelta(days=6)
    assert new.history_days == 7
    assert not new.history_complete
    assert len(new.daily) == 7


def test_failed_full_refetch_preserves_previous_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh, "fetch_full_classifications", fake_full_probe)
    refresh.refresh_source("classifications", through=THROUGH, root=tmp_path, now=NOW)
    before = (tmp_path / "classifications.json").read_bytes()

    def fail(_through):
        raise ValueError("db password leaked by exception")

    monkeypatch.setattr(refresh, "fetch_full_classifications", fail)
    with pytest.raises(ValueError):
        refresh.refresh_source("classifications", through=THROUGH, root=tmp_path, now=NOW)
    assert (tmp_path / "classifications.json").read_bytes() == before


def test_bad_bounded_total_preserves_previous_snapshot(monkeypatch, tmp_path):
    set_history_days(3, root=tmp_path)
    monkeypatch.setattr(refresh, "fetch_window_comments", lambda start, end: fake_probe(start, end))
    refresh.refresh_source("talks", through=THROUGH, root=tmp_path, now=NOW)
    before = (tmp_path / "talks.json").read_bytes()
    monkeypatch.setattr(
        refresh,
        "fetch_window_comments",
        lambda start, end: ClassificationProbe(start, end, 999, ((start, 4),)),
    )
    with pytest.raises(ValueError, match="validation"):
        refresh.refresh_source("talks", through=THROUGH, root=tmp_path, now=NOW)
    assert (tmp_path / "talks.json").read_bytes() == before


def test_panoptes_uses_configured_date_limit(monkeypatch, tmp_path):
    calls = []

    def fetch(*, through, history_days):
        calls.append((through, history_days))
        return {through - timedelta(days=i): 1 for i in range(history_days)}

    monkeypatch.setattr(refresh, "fetch_panoptes", fetch)
    set_history_days(10, root=tmp_path)
    result = refresh.refresh_source("users", through=THROUGH, root=tmp_path, now=NOW)
    assert calls == [(THROUGH, 10)]
    assert result.first == THROUGH - timedelta(days=9)
    assert result.history_days == 10
    assert not result.history_complete


def test_all_streams_status_and_cooldown(monkeypatch, tmp_path):
    calls = []

    def fake_source(source, *, through, root, history_days, now=None):
        assert source in ("users", "classifications", "talks")
        assert through == THROUGH
        calls.append((source, history_days))

    set_history_days(365, root=tmp_path)
    monkeypatch.setattr(refresh, "refresh_source", fake_source)
    launch_calls = []
    monkeypatch.setattr(
        refresh,
        "refresh_launches",
        lambda **kw: launch_calls.append((kw["through"], kw["history_days"])),
    )
    status = refresh.refresh_all(root=tmp_path, now=NOW)
    assert status["state"] == "ok"
    assert calls == [(source, 365) for source in ("users", "classifications", "talks")]
    assert launch_calls == [(THROUGH, 365)]
    assert status["streams"]["project_launches"] == "ok"
    with pytest.raises(refresh.RefreshRejected, match="last hour"):
        refresh.refresh_all(root=tmp_path, now=NOW + timedelta(minutes=5))


def test_failed_source_is_sanitized(monkeypatch, tmp_path):
    def failing(source, **kwargs):
        if source == "users":
            raise ValueError("secret://credentials@example.com")

    monkeypatch.setattr(refresh, "refresh_source", failing)
    monkeypatch.setattr(refresh, "refresh_launches", lambda **kw: None)
    status = refresh.refresh_all(root=tmp_path, now=NOW)
    assert status["state"] == "partial_failure"


@pytest.mark.parametrize(
    ("source", "full_name", "window_name"),
    [
        ("classifications", "fetch_full_classifications", "fetch_window_classifications"),
        ("talks", "fetch_full_comments", "fetch_window_comments"),
    ],
)
def test_activity_fetch_dispatch_retains_full_and_window_contracts(
    monkeypatch, source, full_name, window_name
):
    calls = []

    def full(through):
        calls.append(("full", through))
        return fake_full_probe(through)

    def window(start, through):
        calls.append(("window", start, through))
        return fake_probe(start, through, count=3)

    monkeypatch.setattr(refresh, full_name, full)
    monkeypatch.setattr(refresh, window_name, window)
    full_counts = refresh._fetch_activity_counts(source, through=THROUGH, days=0)
    assert min(full_counts) == date(2024, 1, 1)
    window_counts = refresh._fetch_activity_counts(source, through=THROUGH, days=7)
    assert len(window_counts) == 7
    assert sum(window_counts.values()) == 21
    assert calls == [
        ("full", THROUGH),
        ("window", THROUGH - timedelta(days=6), THROUGH),
    ]


def test_activity_fetch_rejects_wrong_eras_boundaries(monkeypatch):
    monkeypatch.setattr(
        refresh,
        "fetch_window_classifications",
        lambda start, through: fake_probe(start + timedelta(days=1), through),
    )
    with pytest.raises(ValueError, match="date-range aggregate"):
        refresh._fetch_activity_counts("classifications", through=THROUGH, days=7)

    monkeypatch.setattr(
        refresh,
        "fetch_full_comments",
        lambda through: fake_full_probe(through - timedelta(days=1)),
    )
    with pytest.raises(ValueError, match="full-history aggregate"):
        refresh._fetch_activity_counts("talks", through=THROUGH, days=0)
