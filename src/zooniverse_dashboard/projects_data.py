"""Build monthly and annual project-launch summaries from local snapshots.

This module transforms the validated sparse launch series in
:class:`zooniverse_dashboard.project_launches.LaunchSnapshot` into
:class:`zooniverse_dashboard.chart_data.ChartBucket` objects for the
Projects page. It performs no SQL, network access or snapshot writes.

Zero-launch calendar periods are filled only *within* the snapshot's known
coverage. Incomplete first/last periods retain the actual observed bounds;
summary cards distinguish truly zero launches from insufficient history.

See Also
--------
:func:`launch_buckets`
    Group approved launches by calendar month or year.
:func:`project_metrics`
    Compute coverage-aware summary-card values.
:mod:`zooniverse_dashboard.projects_ui`
    Render the aggregated launch history in the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from zooniverse_dashboard.chart_data import (
    ChartBucket,
    group_daily,
)
from zooniverse_dashboard.periods import covered_period_total
from zooniverse_dashboard.project_launches import LaunchSnapshot

INTERVALS: Final[tuple[str, ...]] = ("monthly", "yearly")
WINDOWS: Final[tuple[str, ...]] = ("12", "36", "60", "all")


def launch_buckets(snapshot: LaunchSnapshot, interval: str) -> tuple[ChartBucket, ...]:
    """Group sparse approved launches into consecutive calendar buckets.

    Start with the month or year containing the first covered date and advance
    through the last covered date. Every intervening period is represented even
    when it has no launches. A bucket's ``first_observed`` and ``last_observed``
    are clamped to the snapshot coverage rather than assuming full calendar
    months or years at the edges.

    Parameters
    ----------
    snapshot : :class:`zooniverse_dashboard.project_launches.LaunchSnapshot`
        Validated sparse launch history to aggregate.
    interval : str
        Required grouping: ``"monthly"`` or ``"yearly"``.

    Returns
    -------
    tuple[:class:`zooniverse_dashboard.chart_data.ChartBucket`, ...]
        Chronological calendar buckets with integer approved-launch totals.

    Raises
    ------
    ValueError
        If ``interval`` is not monthly or yearly.

    See Also
    --------
    :func:`zooniverse_dashboard.chart_data.bucket_start`
        Calendar starting date for a given launch day.
    """
    if interval not in INTERVALS:
        raise ValueError("Projects charts support only month and year")
    return group_daily(
        snapshot.launches,
        interval,
        coverage=(snapshot.first, snapshot.last),
    )


def viewport_start(snapshot: LaunchSnapshot, window: str) -> date:
    """Choose the first calendar month of a Projects chart preset.

    The current (possibly partial) month is included in 12-, 36-, and 60-month
    windows. For snapshots with shorter available coverage, the start is
    clamped to :attr:`zooniverse_dashboard.project_launches.LaunchSnapshot.first`.

    Parameters
    ----------
    snapshot : :class:`zooniverse_dashboard.project_launches.LaunchSnapshot`
        Validated launch series defining the last known day and coverage start.
    window : str
        One of ``"12"``, ``"36"``, ``"60"``, or ``"all"``.

    Returns
    -------
    datetime.date
        Earliest displayed UTC date, inclusive.

    Raises
    ------
    ValueError
        If the requested window is unsupported.
    """
    if window not in WINDOWS:
        raise ValueError("Unsupported projects chart window")
    if window == "all":
        return snapshot.first
    months: int = int(window)
    # The current, potentially partial month is the most recent of N months.
    total: int = snapshot.last.year * 12 + snapshot.last.month - 1 - (months - 1)
    start: date = date(total // 12, total % 12 + 1, 1) if total >= 12 else date.min
    return max(snapshot.first, start)


@dataclass(frozen=True)
class ProjectMetric:
    """Describe one coverage-aware summary metric on the Projects page.

    An absent count (``None``) denotes insufficient historical coverage, not
    zero launch events. Current-period totals count completed days only, and
    an incomplete preceding month or year is not extrapolated.

    Attributes
    ----------
    label : str
        Human-readable name of the summary card.
    start : datetime.date
        Earliest date in the requested reporting period, inclusive.
    end : datetime.date
        Last date in the reporting period, inclusive. It can precede ``start``
        when the current period has no completed UTC dates yet.
    count : int or None
        Approved launch count if coverage permits; ``None`` indicates
        insufficient recorded history.
    """

    label: str
    start: date
    end: date
    count: int | None


def project_metrics(snapshot: LaunchSnapshot) -> tuple[ProjectMetric, ...]:
    """Compute total and calendar-to-date launch metrics without extrapolation.

    The first card counts every launch recorded in the snapshot. The remaining
    cards cover current/previous month and current/previous year relative to
    ``snapshot.as_of``. Current periods stop at the last completed UTC day;
    previous periods require the entire historical interval. A current period
    with no completed days yet reports zero. Missing historical coverage yields
    ``None`` instead of an invented zero.

    Parameters
    ----------
    snapshot : :class:`zooniverse_dashboard.project_launches.LaunchSnapshot`
        Validated sparse daily approved-launch counts and coverage metadata.

    Returns
    -------
    tuple[:class:`ProjectMetric`, ...]
        Five immutable summary cards: recorded launches, current month,
        previous month, current year, and previous year, in that order.

    See Also
    --------
    :func:`launch_buckets`
        Build the launch history plotted behind these cards.
    """
    today: date = snapshot.as_of
    yesterday: date = snapshot.last
    month: date = today.replace(day=1)
    previous_month: date = (month - timedelta(days=1)).replace(day=1)
    year: date = date(today.year, 1, 1)
    previous_year: date = date(today.year - 1, 1, 1)
    periods: tuple[tuple[str, date, date], ...] = (
        ("Current month", month, yesterday),
        ("Previous month", previous_month, month - timedelta(days=1)),
        ("Current year", year, yesterday),
        ("Previous year", previous_year, year - timedelta(days=1)),
    )

    def sum_between(start: date, end: date) -> int:
        """Sum the sparse approved launches in a fully covered interval."""
        return sum(value for day, value in snapshot.launches if start <= day <= end)

    metrics: list[ProjectMetric] = [
        ProjectMetric(
            "Recorded launches",
            snapshot.first,
            yesterday,
            sum(value for _, value in snapshot.launches),
        )
    ]
    for label, start, end in periods:
        count: int | None = covered_period_total(
            first=snapshot.first,
            last=snapshot.last,
            start=start,
            end=end,
            sum_between=sum_between,
        )
        metrics.append(ProjectMetric(label, start, end, count))
    return tuple(metrics)
