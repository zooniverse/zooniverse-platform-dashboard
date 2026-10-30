"""Baseline contracts for each existing chart page before UI consolidation."""

from dataclasses import replace

import pytest

from zooniverse_dashboard.page_specs import PAGE_SPECS, Choice
from zooniverse_dashboard.periods import calendar_periods
from zooniverse_dashboard.projects_data import project_metrics


def test_page_specs_are_immutable_and_cover_all_chart_pages():
    assert set(PAGE_SPECS) == {"users", "classifications", "talks", "projects"}
    with pytest.raises(TypeError):
        PAGE_SPECS["new"] = PAGE_SPECS["users"]
    with pytest.raises(AttributeError):
        PAGE_SPECS["users"].title = "changed"
    for key, spec in PAGE_SPECS.items():
        assert spec.key == key
        assert {c.value for c in spec.intervals}
        assert {c.value for c in spec.windows}


def test_activity_pages_share_seven_cards_and_their_options():
    users = PAGE_SPECS["users"]
    assert users.snapshot_kind == "activity"
    assert [(card.key, card.label) for card in users.cards] == [
        ("yesterday", "Yesterday"),
        ("week", "Current week"),
        ("previous-week", "Previous week"),
        ("month", "Current month"),
        ("previous-month", "Previous month"),
        ("year", "Current year"),
        ("previous-year", "Previous year"),
    ]
    assert [c.value for c in users.windows] == ["7", "28", "90", "180", "365", "all"]
    assert [c.value for c in users.intervals] == ["daily", "weekly", "monthly", "yearly"]
    assert (users.default_window, users.default_interval, users.default_plot_type) == (
        "90",
        "daily",
        "line",
    )
    for key in ("classifications", "talks"):
        other = PAGE_SPECS[key]
        assert (other.cards, other.windows, other.intervals, other.plot_types, other.layers) == (
            users.cards,
            users.windows,
            users.intervals,
            users.plot_types,
            users.layers,
        )


def test_projects_has_distinct_periods_and_no_activity_overlays():
    projects = PAGE_SPECS["projects"]
    assert projects.snapshot_kind == "launches"
    assert [card.label for card in projects.cards] == [
        "Recorded launches",
        "Current month",
        "Previous month",
        "Current year",
        "Previous year",
    ]
    assert [c.value for c in projects.windows] == ["12", "36", "60", "all"]
    assert [c.value for c in projects.intervals] == ["monthly", "yearly"]
    assert (projects.default_window, projects.default_interval, projects.default_plot_type) == (
        "36",
        "monthly",
        "bars",
    )
    assert not projects.layers


def test_optional_layers_match_current_trace_policy():
    for key in ("users", "classifications", "talks"):
        page = PAGE_SPECS[key]
        for interval in ("daily", "weekly", "monthly", "yearly"):
            for plot_type in ("line", "bars"):
                assert page.has_layer("project_launches", interval=interval, plot_type=plot_type)
                assert page.has_layer("anomalies", interval=interval, plot_type=plot_type) == (
                    interval == "daily"
                )
                assert page.has_layer(
                    "trailing_average", interval=interval, plot_type=plot_type
                ) == (interval == "daily" and plot_type == "line")
    assert not PAGE_SPECS["projects"].has_layer(
        "project_launches", interval="monthly", plot_type="bars"
    )


def test_spec_validation_rejects_bad_defaults_duplicate_choices_and_invalid_layers():
    page = PAGE_SPECS["users"]
    with pytest.raises(ValueError, match="default"):
        replace(page, default_interval="quarterly")
    with pytest.raises(ValueError, match="unique"):
        replace(page, windows=page.windows + (Choice("90", "Duplicate"),))
    with pytest.raises(ValueError, match="unsupported"):
        replace(page, intervals=PAGE_SPECS["projects"].intervals, default_interval="monthly")


def test_card_labels_match_actual_aggregate_functions():
    """The proposed contract must not silently diverge from calculation semantics."""
    from datetime import date, datetime, timedelta, timezone

    from zooniverse_dashboard.project_launches import LaunchSnapshot
    from zooniverse_dashboard.snapshots import Snapshot

    start = date(2023, 1, 1)
    end = date(2025, 2, 5)
    rows = tuple((start + timedelta(days=n), n % 4) for n in range((end - start).days + 1))
    daily = Snapshot(
        "users",
        start,
        end,
        end + timedelta(days=1),
        datetime(2025, 2, 6, tzinfo=timezone.utc),
        rows,
        True,
        0,
    )
    launches = LaunchSnapshot(
        start,
        end,
        end + timedelta(days=1),
        datetime(2025, 2, 6, tzinfo=timezone.utc),
        0,
        ((date(2024, 3, 1), 2),),
    )
    assert [(p.key, p.label) for p in calendar_periods(daily)] == [
        (card.key, card.label) for card in PAGE_SPECS["users"].cards
    ]
    assert [p.label for p in project_metrics(launches)] == [
        card.label for card in PAGE_SPECS["projects"].cards
    ]
