"""Shared UTC calendar geometry for Plotly activity and project charts.

Grouped bars use equally spaced numeric slots, while date-axis plots keep
actual UTC timestamps. These pure helpers generate tick labels, partial-period
bar widths, and fractional launch-day positions without importing Dash or
reading snapshots. Both the activity and Projects figure builders use this
module, removing their previous cross-page dependency.

See Also
--------
:class:`zooniverse_dashboard.chart_data.ChartBucket`
    Validated calendar bucket boundaries and observed data ranges.
:func:`zooniverse_dashboard.ui.history_figure`
    Activity figure and its optional overlays.
:func:`zooniverse_dashboard.projects_ui.projects_figure`
    Monthly/yearly project launch figure.
"""

from __future__ import annotations

from datetime import date, timedelta

from zooniverse_dashboard.chart_data import ChartBucket


def display_date(day: date) -> str:
    """Format a calendar day consistently in plot hover text.

    Parameters
    ----------
    day : datetime.date
        Calendar day already interpreted as UTC by the caller.

    Returns
    -------
    str
        English abbreviated weekday, day, month and year, such as
        ``Mon 25 May 2026``.
    """
    return day.strftime("%a %d %b %Y")


PERIOD_BAR_WIDTH: float = 0.82


def next_bucket_start(start: date, interval: str) -> date:
    """Compute the exclusive end of a complete calendar bucket.

    Parameters
    ----------
    start : datetime.date
        First date of the bucket (Monday, first of month, or first of year).
    interval : str
        One of ``weekly``, ``monthly``, or ``yearly``.

    Returns
    -------
    datetime.date
        First day of the following calendar period.

    Raises
    ------
    ValueError
        If ``interval`` is not a supported grouped-bar interval.
    """
    if interval == "weekly":
        return start + timedelta(days=7)
    if interval == "monthly":
        if start.month == 12:
            return date(start.year + 1, 1, 1)
        return date(start.year, start.month + 1, 1)
    if interval == "yearly":
        return date(start.year + 1, 1, 1)
    raise ValueError("Only grouped bars use indexed calendar buckets")


def period_label(bucket: ChartBucket, interval: str) -> str:
    """Produce a compact calendar label for a grouped bar axis.

    Parameters
    ----------
    bucket : :class:`zooniverse_dashboard.chart_data.ChartBucket`
        Calendar bucket whose start identifies the displayed period.
    interval : str
        Grouped interval: ``weekly``, ``monthly`` or ``yearly``.

    Returns
    -------
    str
        Week-start date, abbreviated month plus year, or a four-digit year.

    Raises
    ------
    ValueError
        If ``interval`` is not a supported grouped-bar interval.
    """
    if interval == "weekly":
        return bucket.start.strftime("%d %b '%y")
    if interval == "monthly":
        return bucket.start.strftime("%b %Y")
    if interval == "yearly":
        return str(bucket.start.year)
    raise ValueError("Only grouped bars use period labels")


def period_position(day: date, bucket: ChartBucket, index: int, interval: str) -> float:
    """Map one launch day to a fractional coordinate inside a calendar slot.

    Weekly, monthly and yearly bars occupy equally spaced numeric slots. The
    launch's position within the slot is proportional to elapsed calendar days,
    including leap-year months and years.

    Parameters
    ----------
    day : datetime.date
        Launch date within the supplied bucket.
    bucket : :class:`zooniverse_dashboard.chart_data.ChartBucket`
        Bucket containing the launch day.
    index : int
        Zero-based numeric X-coordinate of the corresponding period.
    interval : str
        Grouped-bar interval.

    Returns
    -------
    float
        Fractional numeric X-coordinate within the bar's allotted slot.

    Raises
    ------
    ValueError
        If the interval is unsupported for grouped bars.
    """
    span = (next_bucket_start(bucket.start, interval) - bucket.start).days
    elapsed = (day - bucket.start).days
    return index + PERIOD_BAR_WIDTH * ((elapsed + 0.5) / span - 0.5)


def period_bar_geometry(bucket: ChartBucket, index: int, interval: str) -> tuple[float, float]:
    """Calculate bar center and proportional observed width in a period slot.

    A partial first or last bucket occupies only the fraction of its month,
    week or year covered by the snapshot; the unobserved portion remains empty.

    Parameters
    ----------
    bucket : :class:`zooniverse_dashboard.chart_data.ChartBucket`
        Calendar bucket with first and last observed days.
    index : int
        Integer slot index on the numeric X-axis.
    interval : str
        Weekly, monthly or yearly period selection.

    Returns
    -------
    tuple[float, float]
        Numeric bar center and width, in period-slot units.

    Raises
    ------
    ValueError
        If ``interval`` is not a grouped-bar interval.
    """
    span = (next_bucket_start(bucket.start, interval) - bucket.start).days
    first = (bucket.first_observed - bucket.start).days
    observed = (bucket.last_observed - bucket.first_observed).days + 1
    center = index + PERIOD_BAR_WIDTH * ((first + observed / 2) / span - 0.5)
    return center, PERIOD_BAR_WIDTH * observed / span


def period_ticks(
    first: int, last: int, labels: list[str], *, max_ticks: int = 10
) -> tuple[list[int], list[str]]:
    """Select sparse initial tick locations from indexed calendar labels.

    Browser-side navigation can later update tick density as the view changes.
    The returned indexes are clipped to the supplied label sequence.

    Parameters
    ----------
    first : int
        First visible zero-based period index.
    last : int
        Last visible zero-based period index, inclusive.
    labels : list[str]
        All available calendar-period axis labels.
    max_ticks : int, optional
        Maximum target number of initial labels, default 10.

    Returns
    -------
    tuple[list[int], list[str]]
        Selected numeric tick positions and their matching text labels.
    """
    step = max(1, (last - first + 1 + max_ticks - 1) // max_ticks)
    indexes = list(range(max(0, first), min(len(labels) - 1, last) + 1, step))
    return indexes, [labels[index] for index in indexes]
