"""Pure regression tests for the shared Plotly calendar-axis geometry."""

from datetime import date

import pytest

from zooniverse_dashboard.chart_data import ChartBucket
from zooniverse_dashboard.chart_geometry import (
    PERIOD_BAR_WIDTH,
    display_date,
    next_bucket_start,
    period_bar_geometry,
    period_label,
    period_position,
    period_ticks,
)


def test_weekday_labels_and_calendar_boundaries():
    """Month/year geometry must respect leap years and UTC weekdays."""
    assert display_date(date(2026, 5, 25)) == "Mon 25 May 2026"
    assert next_bucket_start(date(2024, 2, 1), "monthly") == date(2024, 3, 1)
    assert next_bucket_start(date(2024, 1, 1), "yearly") == date(2025, 1, 1)
    assert next_bucket_start(date(2024, 2, 26), "weekly") == date(2024, 3, 4)
    for interval in ("daily", "invalid"):
        with pytest.raises(ValueError):
            next_bucket_start(date(2024, 2, 1), interval)


def test_partial_leap_month_width_and_launch_position():
    """A partially covered February uses 29-day, not 28-day, geometry."""
    bucket = ChartBucket(
        start=date(2024, 2, 1),
        first_observed=date(2024, 2, 10),
        last_observed=date(2024, 2, 20),
        count=8,
    )
    center, width = period_bar_geometry(bucket, 2, "monthly")
    assert center == pytest.approx(2.0)
    assert width == pytest.approx(PERIOD_BAR_WIDTH * 11 / 29)
    assert center - width / 2 < period_position(date(2024, 2, 10), bucket, 2, "monthly")
    assert period_position(date(2024, 2, 20), bucket, 2, "monthly") < center + width / 2
    assert period_label(bucket, "monthly") == "Feb 2024"


def test_year_buckets_and_ticks_are_stable():
    """Calendar periods remain equally spaced in numeric bar coordinates."""
    full = ChartBucket(date(2024, 1, 1), date(2024, 1, 1), date(2024, 12, 31), 10)
    center, width = period_bar_geometry(full, 3, "yearly")
    assert center == pytest.approx(3)
    assert width == pytest.approx(PERIOD_BAR_WIDTH)
    assert period_label(full, "yearly") == "2024"
    assert period_ticks(0, 12, [str(i) for i in range(13)], max_ticks=4) == (
        [0, 4, 8, 12],
        ["0", "4", "8", "12"],
    )
