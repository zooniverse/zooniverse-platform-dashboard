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


def group_daily(
    daily: tuple[tuple[date, int], ...], interval: str = "daily"
) -> tuple[ChartBucket, ...]:
    """Sum chronological daily counts into UTC calendar periods.

    Unlike rate normalization, this operation does not multiply partial
    periods to estimate missing days. The input is expected to be sorted by
    date, as in :attr:`zooniverse_dashboard.snapshots.Snapshot.daily`.

    Parameters
    ----------
    daily : tuple[tuple[datetime.date, int], ...]
        Daily aggregate counts, ordered from oldest to newest.
    interval : str, optional
        Requested bin width, one of :data:`INTERVALS`; defaults to daily.

    Returns
    -------
    tuple[:class:`ChartBucket`, ...]
        Calendar buckets in chronological order, retaining observed edges.

    Raises
    ------
    ValueError
        If ``interval`` is not supported.
    """
    if interval not in INTERVALS:
        raise ValueError("Unsupported aggregation interval")
    buckets: list[ChartBucket] = []
    for day, count in daily:
        beginning: date = bucket_start(day, interval)
        if buckets and buckets[-1].start == beginning:
            old: ChartBucket = buckets[-1]
            buckets[-1] = ChartBucket(beginning, old.first_observed, day, old.count + count)
        else:
            buckets.append(ChartBucket(beginning, day, day, count))
    return tuple(buckets)
