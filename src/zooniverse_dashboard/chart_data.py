"""Transform validated daily aggregates into UTC calendar chart buckets.

These transformations are entirely local: no database calls or network
requests occur while building plots. Day, ISO week, month, and year bins
retain the actual covered dates of partial edge periods, avoiding any
extrapolation of counts beyond the snapshot. Chart interfaces consume
:class:`ChartBucket` rather than making assumptions about source records.

See Also
--------
:class:`zooniverse_dashboard.snapshots.Snapshot`
    Source of validated continuous daily aggregate rows.
:func:`zooniverse_dashboard.projects_data.launch_buckets`
    Similar calendar bucketing for sparse project launches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

INTERVALS: Final[tuple[str, ...]] = ("daily", "weekly", "monthly", "yearly")


@dataclass(frozen=True)
class ChartBucket:
    """One UTC calendar bin with its actually observed date coverage.

    A weekly, monthly, or yearly bin can be partial at the beginning or end
    of a snapshot. ``start`` identifies the full calendar period, while
    ``first_observed`` and ``last_observed`` define its inclusive data range.

    Attributes
    ----------
    start : datetime.date
        First calendar day of the full period (Monday for ISO weeks).
    first_observed : datetime.date
        First date represented by the underlying daily counts.
    last_observed : datetime.date
        Last date represented by the underlying daily counts, inclusive.
    count : int
        Sum of aggregate events in the observed portion of the period.
    """

    start: date
    first_observed: date
    last_observed: date
    count: int

    @property
    def center(self) -> date:
        """Return the midpoint of the observed span for plotting.

        Returns
        -------
        datetime.date
            First observed day plus half the observed span, rounded down to a
            whole number of days.
        """
        return self.first_observed + timedelta(
            days=(self.last_observed - self.first_observed).days // 2
        )

    @property
    def width_ms(self) -> int:
        """Return a slightly narrowed time-based bar width.

        The width covers 88 percent of the observed period, leaving a visible
        gap between adjacent bars. It is for time-based Plotly axes; the
        calendar-slot bar layout may use a different X-axis representation.

        Returns
        -------
        int
            Bar width in milliseconds.
        """
        days: int = (self.last_observed - self.first_observed).days + 1
        return int(days * 86_400_000 * 0.88)


def bucket_start(day: date, interval: str) -> date:
    """Find the calendar-period start for a UTC date and aggregation interval.

    Parameters
    ----------
    day : datetime.date
        Date to classify, interpreted as a UTC calendar day.
    interval : str
        One of ``daily``, ``weekly``, ``monthly``, or ``yearly``. Weeks begin
        on Monday.

    Returns
    -------
    datetime.date
        First date of the containing calendar period.

    Raises
    ------
    ValueError
        If the interval is unsupported.
    """
    if interval == "daily":
        return day
    if interval == "weekly":
        return day - timedelta(days=day.weekday())
    if interval == "monthly":
        return day.replace(day=1)
    if interval == "yearly":
        return date(day.year, 1, 1)
    raise ValueError("Unsupported aggregation interval")


def next_bucket_start(start: date, interval: str) -> date:
    """Return the exclusive end of the calendar period containing ``start``.

    Parameters
    ----------
    start : datetime.date
        A date within the UTC calendar period.
    interval : str
        One of :data:`INTERVALS`, with Monday-starting ISO weeks.

    Returns
    -------
    datetime.date
        First day of the next period.

    Raises
    ------
    ValueError
        If the interval is unsupported or the next date is not representable.
    """
    beginning = bucket_start(start, interval)
    if interval == "daily":
        return beginning + timedelta(days=1)
    if interval == "weekly":
        return beginning + timedelta(days=7)
    if interval == "monthly":
        if beginning.month == 12:
            return date(beginning.year + 1, 1, 1)
        return date(beginning.year, beginning.month + 1, 1)
    if interval == "yearly":
        return date(beginning.year + 1, 1, 1)
    raise ValueError("Unsupported aggregation interval")


def group_daily(
    daily: tuple[tuple[date, int], ...],
    interval: str = "daily",
    *,
    coverage: tuple[date, date] | None = None,
) -> tuple[ChartBucket, ...]:
    """Aggregate daily counts into calendar periods, optionally filling gaps.

    Without ``coverage``, return only observed periods, preserving the
    first/last observed dates inside each bucket. This is the behavior used by
    validated *continuous* :class:`zooniverse_dashboard.snapshots.Snapshot`
    histories. With an inclusive ``coverage=(first, last)``, return **every**
    calendar period overlapping that span, inserting zero-count periods for
    omitted days. The latter mode is for validated *sparse*
    :class:`zooniverse_dashboard.project_launches.LaunchSnapshot` histories;
    it never treats dates outside the supplied coverage as zeros.

    Both modes consume existing daily counts directly: sparse histories are
    not expanded to one row per day. Counts are never extrapolated to full
    periods at the coverage boundaries.

    Parameters
    ----------
    daily : tuple[tuple[datetime.date, int], ...]
        Validated daily counts in increasing date order. For sparse input,
        missing dates are understood to be zero only within ``coverage``.
    interval : str, optional
        One of :data:`INTERVALS`; defaults to daily.
    coverage : tuple[datetime.date, datetime.date] or None, optional
        Inclusive known-coverage bounds. When provided, the output includes
        zero-count periods and clamps their observed edges to these bounds.

    Returns
    -------
    tuple[:class:`ChartBucket`, ...]
        Chronological UTC calendar bins, retaining partial boundary periods.

    Raises
    ------
    ValueError
        If the interval is unsupported, coverage is reversed, or a daily
        observation falls outside the explicitly supplied coverage.
    """
    if interval not in INTERVALS:
        raise ValueError("Unsupported aggregation interval")
    if coverage is not None and coverage[0] > coverage[1]:
        raise ValueError("Invalid calendar coverage")

    grouped: dict[date, ChartBucket] = {}
    for day, count in daily:
        if coverage is not None and not (coverage[0] <= day <= coverage[1]):
            raise ValueError("Daily count outside calendar coverage")
        beginning = bucket_start(day, interval)
        old = grouped.get(beginning)
        if old is not None:
            grouped[beginning] = ChartBucket(beginning, old.first_observed, day, old.count + count)
        else:
            grouped[beginning] = ChartBucket(beginning, day, day, count)

    if coverage is None:
        return tuple(grouped.values())

    first, last = coverage
    result: list[ChartBucket] = []
    beginning = bucket_start(first, interval)
    while beginning <= last:
        following = next_bucket_start(beginning, interval)
        result.append(
            ChartBucket(
                beginning,
                max(beginning, first),
                min(following - timedelta(days=1), last),
                grouped[beginning].count if beginning in grouped else 0,
            )
        )
        beginning = following
    return tuple(result)
