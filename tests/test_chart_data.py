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
    assert focused.layout.meta["history_zoom"]["focused"] is True
    assert focused.layout.uirevision != daily_line.layout.uirevision
    with pytest.raises(ValueError):
        ui.history_figure(snapshot, plot_type="pie")


def test_grouped_bars_are_equal_slots_and_keep_partial_calendar_widths(monkeypatch):
    pytest.importorskip("dash")
    from zooniverse_dashboard import ui

    monkeypatch.setattr(ui, "visible_events", lambda: ([], {}))
    # Jan 30–Feb 4 is a partial January and a partial (leap) February.
    rows = _rows(date(2024, 1, 30), [1] * 6)
    snapshot = Snapshot(
        "users",
        rows[0][0],
        rows[-1][0],
        rows[-1][0] + timedelta(days=1),
        datetime(2024, 2, 6, tzinfo=timezone.utc),
        rows,
        True,
        0,
    )
    monthly = ui.history_figure(snapshot, interval="monthly", plot_type="bars")
    assert monthly.layout.xaxis.type == "linear"
    assert monthly.layout.xaxis.range == (-0.5, 1.5)
    assert monthly.layout.meta["period_labels"] == ["Jan 2024", "Feb 2024"]
    assert monthly.layout.meta["history_zoom"]["period_bounds"] == [
        "2024-01-01",
        "2024-02-01",
        "2024-03-01",
    ]
    assert monthly.data[0].y == (2, 4)
    assert monthly.data[0].width[0] == pytest.approx(0.82 * 2 / 31)
    assert monthly.data[0].width[1] == pytest.approx(0.82 * 4 / 29)
    assert monthly.data[0].x[0] > 0  # Partial late January sits on right edge.
    assert monthly.data[0].x[1] < 1  # Partial early February sits on left edge.
    assert monthly.layout.xaxis.rangeslider.visible
    assert monthly.layout.yaxis.range[1] > 4
    full = _rows(date(2024, 1, 1), [1] * 60)
    fully_covered = Snapshot(
        "users",
        full[0][0],
        full[-1][0],
        full[-1][0] + timedelta(days=1),
        datetime(2024, 3, 2, tzinfo=timezone.utc),
        full,
        True,
        0,
    )
    fig = ui.history_figure(fully_covered, interval="monthly", plot_type="bars")
    assert fig.data[0].x == (0, 1)
    assert fig.data[0].width == pytest.approx((0.82, 0.82))
    assert fig.data[0].customdata[1][0] == "Thu 01 Feb 2024"


def test_week_and_year_bar_labels_and_line_axis_remain_distinct(monkeypatch):
    pytest.importorskip("dash")
    from zooniverse_dashboard import ui

    monkeypatch.setattr(ui, "visible_events", lambda: ([], {}))
    rows = _rows(date(2025, 12, 29), [5] * 42)
    snapshot = Snapshot(
        "users",
        rows[0][0],
        rows[-1][0],
        rows[-1][0] + timedelta(days=1),
        datetime(2026, 2, 11, tzinfo=timezone.utc),
        rows,
        True,
        0,
    )
    weekly = ui.history_figure(snapshot, interval="weekly", plot_type="bars")
    assert weekly.layout.meta["period_labels"][0] == "29 Dec '25"
    assert weekly.layout.meta["period_labels"][1] == "05 Jan '26"
    assert weekly.data[0].x == tuple(range(6))
    assert all(width == pytest.approx(0.82) for width in weekly.data[0].width)
    yearly = ui.history_figure(snapshot, interval="yearly", plot_type="bars")
    assert yearly.layout.meta["period_labels"] == ["2025", "2026"]
    assert yearly.data[0].x[0] > 0  # Only final three days of 2025 observed.
    assert yearly.data[0].x[1] < 1  # Early 2026 is partial.
    line = ui.history_figure(snapshot, interval="weekly", plot_type="line")
    assert line.layout.xaxis.type == "date"
    assert line.layout.meta["history_zoom"]["period_bounds"] is None
    assert line.data[0].x[0] == "2026-01-01"


def test_explicit_coverage_fills_sparse_calendar_periods_without_expanding_days():
    from zooniverse_dashboard.chart_data import next_bucket_start

    sparse = ((date(2024, 1, 31), 3), (date(2024, 3, 2), 2))
    buckets = group_daily(sparse, "monthly", coverage=(date(2024, 1, 30), date(2024, 3, 3)))
    assert [(b.start, b.count) for b in buckets] == [
        (date(2024, 1, 1), 3),
        (date(2024, 2, 1), 0),
        (date(2024, 3, 1), 2),
    ]
    assert [(b.first_observed, b.last_observed) for b in buckets] == [
        (date(2024, 1, 30), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 3)),
    ]
    assert next_bucket_start(date(2024, 12, 30), "weekly") == date(2025, 1, 6)
    assert next_bucket_start(date(2024, 12, 31), "monthly") == date(2025, 1, 1)
    assert next_bucket_start(date(2024, 2, 29), "daily") == date(2024, 3, 1)
    assert next_bucket_start(date(2024, 8, 17), "yearly") == date(2025, 1, 1)


def test_sparse_empty_coverage_is_zero_but_absent_coverage_does_not_invent_periods():
    assert group_daily((), "monthly") == ()
    bins = group_daily((), "monthly", coverage=(date(2025, 12, 30), date(2026, 2, 1)))
    assert [(b.start, b.count) for b in bins] == [
        (date(2025, 12, 1), 0),
        (date(2026, 1, 1), 0),
        (date(2026, 2, 1), 0),
    ]
    assert bins[0].first_observed == date(2025, 12, 30)
    assert bins[-1].last_observed == date(2026, 2, 1)
    with pytest.raises(ValueError, match="coverage"):
        group_daily((), "monthly", coverage=(date(2026, 1, 2), date(2026, 1, 1)))
    with pytest.raises(ValueError, match="outside"):
        group_daily(
            ((date(2025, 12, 31), 2),),
            "monthly",
            coverage=(date(2026, 1, 1), date(2026, 2, 1)),
        )
