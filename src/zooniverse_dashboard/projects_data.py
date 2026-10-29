"""Monthly/yearly approved-launch counts from a validated sparse snapshot.

No database or network access; missing launch dates contribute zero within
snapshot coverage, not missing data. Edge buckets remain partial.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from zooniverse_dashboard.chart_data import ChartBucket, bucket_start
from zooniverse_dashboard.project_launches import LaunchSnapshot

INTERVALS = ("monthly", "yearly")
WINDOWS = ("12", "36", "60", "all")  # completed-day viewport, in calendar months


def next_period(start: date, interval: str) -> date:
    """Exclusive end of the UTC month/year containing *start*."""
    if interval == "monthly":
        if start.month == 12:
            return date(start.year + 1, 1, 1)
        return date(start.year, start.month + 1, 1)
    if interval == "yearly":
        return date(start.year + 1, 1, 1)
    raise ValueError("Projects charts support only month and year")


def launch_buckets(snapshot: LaunchSnapshot, interval: str) -> tuple[ChartBucket, ...]:
    """Include empty calendar periods and use real observed boundaries."""
    if interval not in INTERVALS:
        raise ValueError("Projects charts support only month and year")
    counts = snapshot.counts()
    grouped: dict[date, int] = {}
    for day, value in counts.items():
        start = bucket_start(day, interval)
        grouped[start] = grouped.get(start, 0) + value
    result = []
    start = bucket_start(snapshot.first, interval)
    while start <= snapshot.last:
        end_exclusive = next_period(start, interval)
        result.append(
            ChartBucket(
                start=start,
                first_observed=max(start, snapshot.first),
                last_observed=min(end_exclusive - timedelta(days=1), snapshot.last),
                count=grouped.get(start, 0),
            )
        )
        start = end_exclusive
    return tuple(result)


def viewport_start(snapshot: LaunchSnapshot, window: str) -> date:
    """Start of the last N calendar months, inclusive of the current month."""
    if window not in WINDOWS:
        raise ValueError("Unsupported projects chart window")
    if window == "all":
        return snapshot.first
    months = int(window)
    # The current, potentially partial month is the most recent of N months.
    total = snapshot.last.year * 12 + snapshot.last.month - 1 - (months - 1)
    start = date(total // 12, total % 12 + 1, 1) if total >= 12 else date.min
    return max(snapshot.first, start)


@dataclass(frozen=True)
class ProjectMetric:
    label: str
    start: date
    end: date
    count: int | None


def project_metrics(snapshot: LaunchSnapshot) -> tuple[ProjectMetric, ...]:
    """Calendar-to-date and last *complete* periods, excluding uncovered days."""
    today = snapshot.as_of
    yesterday = snapshot.last
    month = today.replace(day=1)
    previous_month = (month - timedelta(days=1)).replace(day=1)
    year = date(today.year, 1, 1)
    previous_year = date(today.year - 1, 1, 1)
    periods = (
        ("Current month", month, yesterday),
        ("Previous month", previous_month, month - timedelta(days=1)),
        ("Current year", year, yesterday),
        ("Previous year", previous_year, year - timedelta(days=1)),
    )
    metrics = [
        ProjectMetric(
            "Recorded launches",
            snapshot.first,
            yesterday,
            sum(value for _, value in snapshot.launches),
        )
    ]
    for label, start, end in periods:
        if start > end:
            count = 0  # A new month/year with no completed days yet.
        elif start < snapshot.first or end > snapshot.last:
            count = None
        else:
            count = sum(value for day, value in snapshot.launches if start <= day <= end)
        metrics.append(ProjectMetric(label, start, end, count))
    return tuple(metrics)
