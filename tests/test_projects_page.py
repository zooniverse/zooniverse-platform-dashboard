"""Projects uses the approved launch snapshot, without new upstream queries."""

from datetime import date, datetime, timezone

import pytest

from zooniverse_dashboard.project_launches import LaunchSnapshot
from zooniverse_dashboard.projects_data import (
    launch_buckets,
    project_metrics,
    viewport_start,
)


def _snapshot(first=date(2024, 1, 30), last=date(2025, 2, 5), launches=None):
    launches = launches or (
        (date(2024, 1, 30), 1),
        (date(2024, 2, 3), 2),
        (date(2025, 2, 5), 3),
    )
    return LaunchSnapshot(
        first=first,
        last=last,
        as_of=date.fromordinal(last.toordinal() + 1),
        last_modified=datetime(2025, 2, 6, tzinfo=timezone.utc),
        history_days=0,
        launches=launches,
    )


def test_monthly_buckets_fill_missing_months_and_do_not_scale_partial_days():
    snapshot = _snapshot(
        first=date(2024, 1, 30),
        last=date(2024, 4, 3),
        launches=(
            (date(2024, 1, 30), 1),
            (date(2024, 2, 3), 2),
            (date(2024, 4, 2), 3),
        ),
    )
    buckets = launch_buckets(snapshot, "monthly")
    assert [b.count for b in buckets] == [1, 2, 0, 3]
    assert [(b.first_observed, b.last_observed) for b in buckets] == [
        (date(2024, 1, 30), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 31)),
        (date(2024, 4, 1), date(2024, 4, 3)),
    ]


def test_yearly_buckets_preserve_missing_years_and_partial_ends():
    snapshot = _snapshot(
        first=date(2020, 12, 31),
        last=date(2024, 2, 1),
        launches=(
            (date(2020, 12, 31), 1),
            (date(2024, 2, 1), 2),
        ),
    )
    bins = launch_buckets(snapshot, "yearly")
    assert [b.start.year for b in bins] == [2020, 2021, 2022, 2023, 2024]
    assert [b.count for b in bins] == [1, 0, 0, 0, 2]
    assert bins[0].first_observed == bins[0].last_observed == date(2020, 12, 31)
    assert bins[-1].last_observed == date(2024, 2, 1)


def test_viewport_counts_calendar_months_across_new_year_and_leap_year():
    snap = _snapshot()
    assert viewport_start(snap, "12") == date(2024, 3, 1)
    assert viewport_start(snap, "36") == snap.first
    assert viewport_start(snap, "all") == snap.first
    with pytest.raises(ValueError):
        viewport_start(snap, "7")
    with pytest.raises(ValueError):
        launch_buckets(snap, "weekly")


def test_kpis_disclose_incomplete_history_instead_of_assuming_zero():
    recent = _snapshot(
        first=date(2025, 2, 1),
        last=date(2025, 2, 5),
        launches=((date(2025, 2, 3), 2),),
    )
    metrics = {m.label: m.count for m in project_metrics(recent)}
    assert metrics == {
        "Recorded launches": 2,
        "Current month": 2,
        "Previous month": None,
        "Current year": None,
        "Previous year": None,
    }
    complete = _snapshot(
        first=date(2023, 1, 1),
        last=date(2025, 2, 5),
        launches=(
            (date(2024, 1, 3), 5),
            (date(2025, 1, 7), 1),
            (date(2025, 2, 2), 2),
        ),
    )
    values = {m.label: m.count for m in project_metrics(complete)}
    assert values["Current month"] == 2
    assert values["Previous month"] == 1
    assert values["Current year"] == 3
    assert values["Previous year"] == 5


def test_new_month_with_no_completed_days_is_zero():
    snap = _snapshot(
        first=date(2024, 1, 1),
        last=date(2025, 1, 31),
        launches=((date(2024, 11, 15), 1),),
    )
    metrics = {m.label: m.count for m in project_metrics(snap)}
    assert metrics["Current month"] == 0
    assert metrics["Previous month"] == 0


def test_figure_monthly_and_yearly_share_zoom_conversions():
    pytest.importorskip("dash")
    from zooniverse_dashboard.projects_ui import projects_figure

    snap = _snapshot()
    monthly = projects_figure(snap, interval="monthly", plot_type="bars", window="all")
    meta = monthly.layout.meta["history_zoom"]
    assert meta["source"] == "projects"
    assert meta["period_bounds"][0] == "2024-01-01"
    assert meta["period_bounds"][-1] == "2025-03-01"
    assert len(meta["period_bounds"]) == len(monthly.data[0].x) + 1
    assert monthly.data[0].y[2] == 0  # March 2024 had no launches
    assert monthly.data[0].width[0] == pytest.approx(0.82 * 2 / 31)
    assert monthly.layout.meta["period_labels"][0] == "Jan 2024"
    yearly = projects_figure(snap, interval="yearly", plot_type="bars", window="all")
    assert yearly.data[0].y == (3, 3)
    assert yearly.layout.meta["history_zoom"]["period_bounds"][-1] == "2026-01-01"
    line = projects_figure(snap, interval="monthly", plot_type="line", window="12")
    assert line.layout.xaxis.type == "date"
    assert line.layout.xaxis.range[0] == "2024-03-01"
    assert line.layout.meta["history_zoom"]["period_bounds"] is None
    for forbidden in ("daily", "weekly"):
        with pytest.raises(ValueError):
            projects_figure(snap, interval=forbidden)


def test_projects_navigation_and_controls():
    pytest.importorskip("dash")
    from zooniverse_dashboard import projects_ui

    assert any(
        page["relative_path"] == "/projects" for page in __import__("dash").page_registry.values()
    )
    graph = projects_ui.projects_figure(_snapshot())
    assert graph.layout.meta["history_zoom"]["window"] == "36"
    assert graph.layout.yaxis.title.text == "Projects launched per month"
