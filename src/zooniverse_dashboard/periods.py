"""Build UTC calendar summary metrics and initial chart date ranges.

Seven user/activity KPIs are calculated exclusively from validated daily
aggregate snapshots. Missing historical coverage yields ``None`` rather
than treating unknown days as zero; current periods include only fully
completed UTC days. The ``DailyAggregateSeries`` protocol allows callers
with compatible daily time series to reuse the calculation.

See Also
--------
:class:`zooniverse_dashboard.snapshots.Snapshot`
    Standard persisted daily aggregate series.
:func:`zooniverse_dashboard.projects_data.project_metrics`
    Corresponding summaries for sparse project launches.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Protocol


class DailyAggregateSeries(Protocol):
    """Structural contract for continuous UTC daily aggregate snapshots.

    Classes implementing these attributes can be passed to
    :func:`calendar_periods`; the protocol itself performs no validation.

    Attributes
    ----------
    first : datetime.date
        First available UTC date, inclusive.
    last : datetime.date
        Last available completed UTC date, inclusive.
    as_of : datetime.date
        Midnight UTC immediately after ``last``.
    daily : tuple[tuple[datetime.date, int], ...]
        Nonnegative daily counts, one entry per date from ``first`` to
        ``last``, in ascending order.
    """

    first: date
    last: date
    as_of: date
    daily: tuple[tuple[date, int], ...]


@dataclass(frozen=True)
class CalendarPeriod:
    """Named inclusive UTC interval and its validated aggregate count.

    Attributes
    ----------
    key : str
        Machine-readable identifier used to distinguish KPI periods.
    label : str
        Human-readable display label.
    start : datetime.date
        Inclusive first calendar date of the interval.
    end : datetime.date
        Inclusive last calendar date (can precede ``start`` if no completed
        days exist in the current period).
    count : int or None
        Total event count; ``None`` when the snapshot lacks complete coverage.
    """

    key: str
    label: str
    start: date
    end: date
    count: int | None


def _previous_month_start(day: date) -> date:
    """Find the first day of the month before the given UTC date.

    Parameters
    ----------
    day : datetime.date
        Reference calendar date.

    Returns
    -------
    datetime.date
        First day of the preceding month, including across year boundaries.
    """
    month_start: date = day.replace(day=1)
    return (month_start - timedelta(days=1)).replace(day=1)


def covered_period_total(
    *,
    first: date,
    last: date,
    start: date,
    end: date,
    sum_between: Callable[[date, date], int],
) -> int | None:
    """Calculate a period total only when all requested dates are covered.

    This helper implements the *shared coverage rule* for both continuous
    activity snapshots and sparse project-launch snapshots. It does **not**
    assume missing daily records mean zero: each caller provides a summation
    function appropriate for its already-validated representation.

    An empty current calendar interval has a total of zero, even when no
    completed day of the new period exists yet. A nonempty interval extending
    beyond the snapshot coverage has an unknown total (``None``); it is never
    extrapolated or silently zero-filled.

    Parameters
    ----------
    first, last : datetime.date
        Inclusive first and last days of validated snapshot coverage.
    start, end : datetime.date
        Requested inclusive calendar interval. ``start > end`` denotes an
        interval with no completed days.
    sum_between : Callable[[datetime.date, datetime.date], int]
        Called only for nonempty, fully covered intervals; returns their total.

    Returns
    -------
    int or None
        Zero for an empty interval, ``None`` for incomplete coverage, or the
        source-specific total for a fully covered interval.
    """
    if start > end:
        return 0
    if start < first or end > last:
        return None
    return sum_between(start, end)


def calendar_periods(snapshot: DailyAggregateSeries) -> tuple[CalendarPeriod, ...]:
    """Calculate seven calendar KPIs from a continuous daily aggregate series.

    The periods are yesterday, current and previous week, current and
    previous month, and current and previous year. ISO weeks start Monday.
    Current periods end on the last completed UTC day; a just-started
    period with no completed days has count zero. An otherwise incomplete
    historical period is reported as unavailable, never estimated.

    Parameters
    ----------
    snapshot : :class:`DailyAggregateSeries`
        Validated daily series with consecutive dates and an ``as_of`` date
        following the last completed UTC day.

    Returns
    -------
    tuple[:class:`CalendarPeriod`, ...]
        The seven ordered periods with totals or ``None`` for incomplete
        historical coverage.
    """
    today: date = snapshot.as_of
    yesterday: date = today - timedelta(days=1)
    current_week: date = today - timedelta(days=today.weekday())
    previous_week: date = current_week - timedelta(days=7)
    current_month: date = today.replace(day=1)
    previous_month: date = _previous_month_start(today)
    current_year: date = date(today.year, 1, 1)
    previous_year: date = date(today.year - 1, 1, 1)
    periods = (
        ("yesterday", "Yesterday", yesterday, yesterday),
        ("week", "Current week", current_week, yesterday),
        ("previous-week", "Previous week", previous_week, current_week - timedelta(days=1)),
        ("month", "Current month", current_month, yesterday),
        ("previous-month", "Previous month", previous_month, current_month - timedelta(days=1)),
        ("year", "Current year", current_year, yesterday),
        ("previous-year", "Previous year", previous_year, current_year - timedelta(days=1)),
    )
    # A single prefix sum makes all the calendar totals constant-time.
    running: list[int] = [0]
    for _, count in snapshot.daily:
        running.append(running[-1] + count)

    def sum_between(start: date, end: date) -> int:
        """Sum consecutive daily counts using the validated prefix table."""
        left: int = (start - snapshot.first).days
        right: int = (end - snapshot.first).days + 1
        return running[right] - running[left]

    results: list[CalendarPeriod] = []
    for key, label, start, end in periods:
        value: int | None = covered_period_total(
            first=snapshot.first,
            last=snapshot.last,
            start=start,
            end=end,
            sum_between=sum_between,
        )
        results.append(CalendarPeriod(key, label, start, end, value))
    return tuple(results)


def visible_date_range(*, first: date, last: date, window: str) -> tuple[date, date]:
    """Choose the inclusive initial chart viewport within snapshot coverage.

    Parameters
    ----------
    first : datetime.date
        Earliest date available in the aggregate snapshot.
    last : datetime.date
        Latest completed date available in the snapshot.
    window : str
        ``all`` for the complete history, or one of ``7``, ``28``, ``90``,
        ``180``, or ``365`` to select the most recent calendar days.

    Returns
    -------
    tuple[datetime.date, datetime.date]
        Inclusive first and last dates of the chosen viewport, clamped to
        the available snapshot range.

    Raises
    ------
    ValueError
        If ``first`` is after ``last`` or ``window`` is not supported.
    """
    if first > last:
        raise ValueError("empty dataset date span")
    if window == "all":
        return first, last
    if window not in {"7", "28", "90", "180", "365"}:
        raise ValueError("unsupported history window")
    return max(first, last - timedelta(days=int(window) - 1)), last
