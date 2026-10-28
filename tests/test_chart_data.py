"""Calendar aggregation and snapshot-only Plotly history controls."""

from datetime import date, datetime, timedelta, timezone

import pytest

from zooniverse_dashboard.chart_data import bucket_start, group_daily
from zooniverse_dashboard.snapshots import Snapshot


def _rows(start, counts):
    return tuple((start + timedelta(days=i), count) for i, count in enumerate(counts))


def test_calendar_boundaries_and_partial_periods():
    # Jan 29, 2024 is Monday; February has 29 days.
    rows = _rows(date(2024, 1, 29), [1] * 39)  # Through March 7.
    weekly = group_daily(rows, "weekly")
    assert [(b.start, b.count) for b in weekly] == [
        (date(2024, 1, 29), 7),
        (date(2024, 2, 5), 7),
        (date(2024, 2, 12), 7),
        (date(2024, 2, 19), 7),
        (date(2024, 2, 26), 7),
        (date(2024, 3, 4), 4),
    ]
    monthly = group_daily(rows, "monthly")
    assert [(b.start, b.count) for b in monthly] == [
        (date(2024, 1, 1), 3),
        (date(2024, 2, 1), 29),
        (date(2024, 3, 1), 7),
    ]
    assert monthly[0].first_observed == date(2024, 1, 29)
    assert monthly[-1].last_observed == date(2024, 3, 7)
    assert sum(b.count for b in monthly) == sum(count for _, count in rows)
    assert sum(b.count for b in weekly) == 39
    assert group_daily(rows, "daily")[0].count == 1
    assert group_daily(rows, "yearly")[0].count == 39


def test_year_boundary_zeroes_and_partial_start():
    rows = _rows(date(2025, 12, 30), [0, 5, 2, 0, 10])
    assert [(b.start, b.count) for b in group_daily(rows, "yearly")] == [
        (date(2025, 1, 1), 5),
        (date(2026, 1, 1), 12),
    ]
    assert bucket_start(date(2026, 1, 4), "weekly") == date(2025, 12, 29)
    with pytest.raises(ValueError):
        group_daily(rows, "quarterly")


def test_chart_modes_initial_scale_and_episode_navigation(monkeypatch):
    pytest.importorskip("dash")
    from zooniverse_dashboard import ui

    monkeypatch.setattr(ui, "visible_events", lambda: ([], {}))
    daily = _rows(date(2026, 1, 1), [4] * 120 + [800] + [6] * 90)
    snapshot = Snapshot(
        "users",
        daily[0][0],
        daily[-1][0],
        daily[-1][0] + timedelta(days=1),
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        daily,
        True,
        0,
    )
    for interval, expected in (("daily", 211), ("weekly", 31), ("monthly", 7), ("yearly", 1)):
        fig = ui.history_figure(snapshot, interval=interval, plot_type="bars", window="28")
        assert fig.layout.dragmode == "zoom"
        assert fig.data[0].type == "bar"
        assert len(fig.data[0].y) == expected
        assert sum(fig.data[0].y) == sum(count for _, count in daily)
        assert fig.layout.yaxis.range[0] == 0
        if interval in ("daily", "weekly"):
            assert fig.layout.yaxis.range[1] < 100  # Distant spike outside the view.
        elif interval == "monthly":
            assert fig.layout.yaxis.range[1] < 250  # Full month total, no distant spike.
        else:
            assert fig.layout.yaxis.range[1] > 1000  # Annual total includes the spike.
    daily_line = ui.history_figure(snapshot, interval="daily", plot_type="line", window="28")
    assert len(daily_line.data) == 2  # Counts and trailing average, no events mocked.
    monthly_line = ui.history_figure(snapshot, interval="monthly", plot_type="line")
    assert len(monthly_line.data) == 1
    assert monthly_line.data[0].mode == "lines+markers"
    focus = {"source": "users", "start": "2026-05-01", "end": "2026-05-03", "id": "episode"}
    focused = ui.history_figure(snapshot, focus_event=focus)
    assert focused.layout.xaxis.range[0] == "2026-04-24"
    assert focused.layout.uirevision != daily_line.layout.uirevision
    with pytest.raises(ValueError):
        ui.history_figure(snapshot, plot_type="pie")


def test_daily_bar_keeps_clickable_episode_markers(monkeypatch):
    pytest.importorskip("dash")
    from zooniverse_dashboard import ui

    rows = _rows(date(2026, 5, 1), [4, 12, 3, 8, 2])
    snapshot = Snapshot(
        "users",
        rows[0][0],
        rows[-1][0],
        rows[-1][0] + timedelta(days=1),
        datetime(2026, 5, 7, tzinfo=timezone.utc),
        rows,
        True,
        0,
    )
    event = {
        "id": "users-2026-05-02-spike",
        "source": "users",
        "direction": "spike",
        "detected": "2026-05-02",
        "start": "2026-05-02",
        "end": "2026-05-03",
    }
    monkeypatch.setattr(ui, "visible_events", lambda: ([event], {"users": "ready"}))
    daily = ui.history_figure(snapshot, plot_type="bars", interval="daily")
    assert daily.data[0].type == "bar"
    assert daily.data[1].type == "scatter"
    assert daily.data[1].customdata[0][0] == event["id"]
    monthly = ui.history_figure(snapshot, plot_type="bars", interval="monthly")
    assert len(monthly.data) == 1  # Do not move daily detection dates to month bins.
